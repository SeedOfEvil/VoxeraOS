from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import (
    files_copy,
    files_copy_file,
    files_delete_file,
    files_exists,
    files_find,
    files_grep_text,
    files_list_dir,
    files_list_tree,
    files_mkdir,
    files_move,
    files_move_file,
    files_read_text,
    files_rename,
    files_stat,
    files_write_text,
)


def _error_class(result):
    return result.data[SKILL_RESULT_KEY]["error_class"]


def test_read_text_rejects_queue_control_plane_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    queue_dir.mkdir(parents=True)
    target = queue_dir / "job.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)

    rr = files_read_text.run(str(target))

    assert rr.ok is False
    assert _error_class(rr) == "path_blocked_scope"


def test_write_text_rejects_queue_control_plane_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    (allowed_root / "queue").mkdir(parents=True)
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    rr = files_write_text.run(path="queue/new.txt", text="x")

    assert rr.ok is False
    assert _error_class(rr) == "path_blocked_scope"


def test_list_dir_rejects_queue_control_plane_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    queue_dir.mkdir(parents=True)
    monkeypatch.setattr(files_list_dir, "ALLOWED_ROOT", allowed_root)

    rr = files_list_dir.run(str(queue_dir))

    assert rr.ok is False
    assert _error_class(rr) == "path_blocked_scope"


def test_copy_file_rejects_control_plane_source_and_destination(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    user_dir = allowed_root / "user"
    queue_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    queue_source = queue_dir / "job.json"
    user_source = user_dir / "a.txt"
    queue_source.write_text("{}", encoding="utf-8")
    user_source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_copy_file, "ALLOWED_ROOT", allowed_root)

    rr_source = files_copy_file.run(str(queue_source), str(user_dir / "b.txt"))
    rr_destination = files_copy_file.run(str(user_source), str(queue_dir / "copied.txt"))

    assert rr_source.ok is False
    assert _error_class(rr_source) == "path_blocked_scope"
    assert rr_destination.ok is False
    assert _error_class(rr_destination) == "path_blocked_scope"


def test_move_file_rejects_control_plane_source_and_destination(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    user_dir = allowed_root / "user"
    queue_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    queue_source = queue_dir / "job.json"
    user_source = user_dir / "a.txt"
    queue_source.write_text("{}", encoding="utf-8")
    user_source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_move_file, "ALLOWED_ROOT", allowed_root)

    rr_source = files_move_file.run(str(queue_source), str(user_dir / "b.txt"))
    rr_destination = files_move_file.run(str(user_source), str(queue_dir / "moved.txt"))

    assert rr_source.ok is False
    assert _error_class(rr_source) == "path_blocked_scope"
    assert rr_destination.ok is False
    assert _error_class(rr_destination) == "path_blocked_scope"


def test_wave2_file_skills_reject_queue_control_plane_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    queue_dir.mkdir(parents=True)
    queue_file = queue_dir / "job.json"
    queue_file.write_text("{}", encoding="utf-8")

    for module in (files_exists, files_stat, files_mkdir, files_delete_file):
        monkeypatch.setattr(module, "ALLOWED_ROOT", allowed_root)

    rr_exists = files_exists.run("queue/job.json")
    rr_stat = files_stat.run("queue/job.json")
    rr_mkdir = files_mkdir.run("queue/new")
    rr_delete = files_delete_file.run("queue/job.json")

    assert rr_exists.ok is False
    assert _error_class(rr_exists) == "path_blocked_scope"
    assert rr_stat.ok is False
    assert _error_class(rr_stat) == "path_blocked_scope"
    assert rr_mkdir.ok is False
    assert _error_class(rr_mkdir) == "path_blocked_scope"
    assert rr_delete.ok is False
    assert _error_class(rr_delete) == "path_blocked_scope"


def test_files_workspace_expansion_skills_reject_queue_control_plane_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    queue_dir = allowed_root / "queue"
    queue_dir.mkdir(parents=True)
    user_dir = allowed_root / "user"
    user_dir.mkdir(parents=True)
    queue_file = queue_dir / "job.txt"
    queue_file.write_text("secret", encoding="utf-8")
    user_file = user_dir / "ok.txt"
    user_file.write_text("ok", encoding="utf-8")

    for module in (
        files_find,
        files_grep_text,
        files_list_tree,
        files_copy,
        files_move,
        files_rename,
    ):
        monkeypatch.setattr(module, "ALLOWED_ROOT", allowed_root)

    rr_find = files_find.run("queue")
    rr_grep = files_grep_text.run("queue", pattern="secret")
    rr_tree = files_list_tree.run("queue")
    rr_copy = files_copy.run(str(user_file), str(queue_dir / "copy.txt"))
    rr_move = files_move.run(str(user_file), str(queue_dir / "move.txt"))
    rr_rename = files_rename.run(str(queue_file), "renamed.txt")

    assert rr_find.ok is False
    assert _error_class(rr_find) == "path_blocked_scope"
    assert rr_grep.ok is False
    assert _error_class(rr_grep) == "path_blocked_scope"
    assert rr_tree.ok is False
    assert _error_class(rr_tree) == "path_blocked_scope"
    assert rr_copy.ok is False
    assert _error_class(rr_copy) == "path_blocked_scope"
    assert rr_move.ok is False
    assert _error_class(rr_move) == "path_blocked_scope"
    assert rr_rename.ok is False
    assert _error_class(rr_rename) == "path_blocked_scope"
