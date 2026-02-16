from voxera_builtin_skills import files_write_text


def test_write_text_overwrite_and_append_modes(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    target = allowed_root / "mission.txt"

    rr1 = files_write_text.run(path=str(target), text="hello", mode="overwrite")
    rr2 = files_write_text.run(path=str(target), text=" world", mode="append")

    assert rr1.ok is True
    assert rr2.ok is True
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_text_rejects_invalid_mode(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    target = allowed_root / "mission.txt"
    rr = files_write_text.run(path=str(target), text="x", mode="bad")

    assert rr.ok is False
    assert "mode must be append or overwrite" in (rr.error or "")


def test_write_text_rejects_outside_allowlist(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    outside = tmp_path / "outside.txt"
    rr = files_write_text.run(path=str(outside), text="x")

    assert rr.ok is False
    assert "outside allowlist" in (rr.error or "")
