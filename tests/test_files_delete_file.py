from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_delete_file


def test_delete_file_flags_control_plane_scope_as_blocked(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    blocked_target = allowed_root / "queue" / "blocked.txt"
    blocked_target.parent.mkdir(parents=True)
    blocked_target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_delete_file, "ALLOWED_ROOT", allowed_root)

    rr = files_delete_file.run(str(blocked_target))

    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "path_blocked_scope"
    assert payload["blocked"] is True
