from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_write_text


def test_write_text_absolute_allowed_path_succeeds(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    target = allowed_root / "mission.txt"

    rr1 = files_write_text.run(path=str(target), text="hello", mode="overwrite")
    rr2 = files_write_text.run(path=str(target), text=" world", mode="append")

    assert rr1.ok is True
    assert rr2.ok is True
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_text_relative_path_writes_under_allowed_root(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    rr = files_write_text.run(path="ok.txt", text="ok", mode="overwrite")

    assert rr.ok is True
    assert (allowed_root / "ok.txt").read_text(encoding="utf-8") == "ok"


def test_write_text_rejects_invalid_mode(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    target = allowed_root / "mission.txt"
    rr = files_write_text.run(path=str(target), text="x", mode="bad")

    assert rr.ok is False
    assert "mode must be append or overwrite" in (rr.error or "")


def test_write_text_rejects_outside_allowlist_with_requested_path_in_error(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    outside = tmp_path / "outside" / "notes.txt"
    rr = files_write_text.run(path=str(outside), text="x")

    assert rr.ok is False
    assert "allowlist" in (rr.error or "")
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"


def test_write_text_rejects_relative_traversal(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    rr = files_write_text.run(path="../escape.txt", text="x")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"


# ---------------------------------------------------------------------------
# Regression: machine_payload must include bounded content for output review
# ---------------------------------------------------------------------------


def test_write_text_result_includes_content_in_machine_payload(tmp_path, monkeypatch):
    """Regression: 'What was the output?' for a file-writing job must surface
    the actual written content. The skill must include bounded content in
    machine_payload so result_surfacing can extract it."""
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    joke = "Why did the queue cross the road? To get to done."
    rr = files_write_text.run(
        path=str(allowed_root / "test-output-note.txt"),
        text=joke,
        mode="overwrite",
    )

    assert rr.ok is True
    skill_result = rr.data[SKILL_RESULT_KEY]
    payload = skill_result["machine_payload"]
    assert payload["content"] == joke
    assert payload["bytes"] == len(joke.encode("utf-8"))
    assert payload["content_truncated"] is False


def test_write_text_result_truncates_large_content(tmp_path, monkeypatch):
    """Large writes must include a bounded excerpt, not the full text."""
    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)

    large_text = "x" * 5000
    rr = files_write_text.run(
        path=str(allowed_root / "big.txt"),
        text=large_text,
        mode="overwrite",
    )

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["content_truncated"] is True
    assert len(payload["content"]) == 2048
    assert payload["content"] == large_text[:2048]
