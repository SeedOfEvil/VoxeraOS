from __future__ import annotations

from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_delete_file, files_exists, files_mkdir, files_stat


def test_files_mkdir_creates_directory(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_mkdir, "ALLOWED_ROOT", allowed_root)

    rr = files_mkdir.run("projects/demo", parents=True, exist_ok=True)

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["created"] is True
    assert (allowed_root / "projects" / "demo").is_dir()


def test_files_exists_reports_file_presence(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    target = allowed_root / "work" / "todo.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_exists, "ALLOWED_ROOT", allowed_root)

    rr = files_exists.run("work/todo.txt")

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["exists"] is True
    assert payload["kind"] == "file"


def test_files_stat_returns_basic_metadata(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    target = allowed_root / "meta.txt"
    target.parent.mkdir(parents=True)
    target.write_text("abc", encoding="utf-8")
    monkeypatch.setattr(files_stat, "ALLOWED_ROOT", allowed_root)

    rr = files_stat.run("meta.txt")

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["size_bytes"] == 3
    assert payload["kind"] == "file"
    assert payload["modified_ts"].endswith("+00:00")


def test_files_delete_file_deletes_regular_file(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    target = allowed_root / "trash" / "obsolete.txt"
    target.parent.mkdir(parents=True)
    target.write_text("obsolete", encoding="utf-8")
    monkeypatch.setattr(files_delete_file, "ALLOWED_ROOT", allowed_root)

    rr = files_delete_file.run("trash/obsolete.txt")

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["deleted"] is True
    assert not target.exists()


def test_files_delete_file_rejects_directory_target(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    target = allowed_root / "trash"
    target.mkdir(parents=True)
    monkeypatch.setattr(files_delete_file, "ALLOWED_ROOT", allowed_root)

    rr = files_delete_file.run("trash")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "invalid_input"
