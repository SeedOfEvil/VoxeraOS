"""Tests for bounded file intent classification and Vera handoff integration."""

from __future__ import annotations

from voxera.core.file_intent import classify_bounded_file_intent
from voxera.vera.handoff import maybe_draft_job_payload, normalize_preview_payload

# ---------------------------------------------------------------------------
# classify_bounded_file_intent: existence checks
# ---------------------------------------------------------------------------


def test_exists_check_if_file_exists():
    for msg in (
        "check if a.txt exists",
        "check whether a.txt exists",
        "does a.txt exist",
        "is a.txt there",
        "see if a.txt exists",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is not None, f"should match: {msg}"
        assert result["steps"][0]["skill_id"] == "files.exists"
        assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/a.txt"


def test_exists_with_explicit_path():
    result = classify_bounded_file_intent("check if ~/VoxeraOS/notes/inbox/today.md exists")
    assert result is not None
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/inbox/today.md"


def test_exists_rejects_queue_path():
    result = classify_bounded_file_intent("check if ~/VoxeraOS/notes/queue/inbox.json exists")
    assert result is None


def test_exists_rejects_parent_traversal():
    result = classify_bounded_file_intent("check if ../../../etc/passwd exists")
    assert result is None


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: stat/info
# ---------------------------------------------------------------------------


def test_stat_show_info():
    for msg in (
        "show me info about file.txt",
        "show me information about file.txt",
        "details about file.txt",
        "metadata for file.txt",
        "file info file.txt",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is not None, f"should match: {msg}"
        assert result["steps"][0]["skill_id"] == "files.stat"
        assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/file.txt"


def test_stat_with_explicit_path():
    result = classify_bounded_file_intent("show me info about ~/VoxeraOS/notes/report.txt")
    assert result is not None
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/report.txt"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: mkdir
# ---------------------------------------------------------------------------


def test_mkdir_make_folder():
    for msg in (
        "make a folder called testdir in my notes",
        "create folder testdir",
        "create a directory called testdir",
        "make a folder called testdir",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is not None, f"should match: {msg}"
        assert result["steps"][0]["skill_id"] == "files.mkdir"
        assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/testdir"
        assert result["steps"][0]["args"]["parents"] is True


def test_mkdir_rejects_queue_path():
    result = classify_bounded_file_intent("make a folder called queue")
    assert result is None


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: delete
# ---------------------------------------------------------------------------


def test_delete_file():
    for msg in (
        "delete temp.txt",
        "remove temp.txt",
        "delete the file temp.txt",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is not None, f"should match: {msg}"
        assert result["steps"][0]["skill_id"] == "files.delete_file"
        assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/temp.txt"


def test_delete_rejects_queue_path():
    result = classify_bounded_file_intent("delete ~/VoxeraOS/notes/queue/inbox.json")
    assert result is None


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: copy
# ---------------------------------------------------------------------------


def test_copy_file_to_directory():
    result = classify_bounded_file_intent("copy report.txt into receipts")
    assert result is not None
    assert "file_organize" in result
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/report.txt"
    assert result["file_organize"]["destination_dir"] == "~/VoxeraOS/notes/receipts"
    assert result["file_organize"]["mode"] == "copy"


def test_copy_file_to_file():
    result = classify_bounded_file_intent("copy a.txt to b.txt")
    assert result is not None
    assert "file_organize" in result
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/a.txt"
    assert result["file_organize"]["mode"] == "copy"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: move
# ---------------------------------------------------------------------------


def test_move_file():
    result = classify_bounded_file_intent("move a.txt to archive.txt")
    assert result is not None
    assert "file_organize" in result
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/a.txt"
    assert result["file_organize"]["mode"] == "move"


def test_move_file_to_directory():
    result = classify_bounded_file_intent("move report.txt into archive")
    assert result is not None
    assert result["file_organize"]["destination_dir"] == "~/VoxeraOS/notes/archive"
    assert result["file_organize"]["mode"] == "move"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: archive/organize
# ---------------------------------------------------------------------------


def test_archive_note_into_folder():
    result = classify_bounded_file_intent("archive today.md into my archive folder")
    assert result is not None
    assert "file_organize" in result
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/today.md"
    assert "archive" in result["file_organize"]["destination_dir"]
    assert result["file_organize"]["mode"] == "copy"


def test_archive_with_explicit_paths():
    result = classify_bounded_file_intent(
        "archive ~/VoxeraOS/notes/inbox/report.txt into ~/VoxeraOS/notes/archive/2026-03"
    )
    assert result is not None
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/inbox/report.txt"
    assert result["file_organize"]["destination_dir"] == "~/VoxeraOS/notes/archive/2026-03"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: fail-closed
# ---------------------------------------------------------------------------


def test_ambiguous_returns_none():
    for msg in (
        "do something with the files",
        "help me organize my stuff",
        "what files do I have",
        "",
        "hello",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is None, f"should not match: {msg}"


def test_outside_notes_scope_returns_none():
    result = classify_bounded_file_intent("delete /etc/passwd")
    assert result is None


# ---------------------------------------------------------------------------
# Handoff integration: maybe_draft_job_payload
# ---------------------------------------------------------------------------


def test_handoff_routes_exists_check():
    result = maybe_draft_job_payload("check if a.txt exists")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.exists"


def test_handoff_routes_stat():
    result = maybe_draft_job_payload("show me info about file.txt")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.stat"


def test_handoff_routes_mkdir():
    result = maybe_draft_job_payload("make a folder called testdir in my notes")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.mkdir"


def test_handoff_routes_move():
    result = maybe_draft_job_payload("move a.txt to archive.txt")
    assert result is not None
    assert "file_organize" in result


def test_handoff_routes_archive():
    result = maybe_draft_job_payload("archive this note today.md into my archive folder")
    assert result is not None
    assert "file_organize" in result


def test_handoff_routes_copy():
    result = maybe_draft_job_payload("copy report.txt into receipts")
    assert result is not None
    assert "file_organize" in result


def test_handoff_routes_delete():
    result = maybe_draft_job_payload("delete temp.txt")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.delete_file"


# ---------------------------------------------------------------------------
# normalize_preview_payload: file_organize and steps
# ---------------------------------------------------------------------------


def test_normalize_preview_payload_accepts_file_organize():
    payload = {
        "goal": "copy report.txt into receipts",
        "file_organize": {
            "source_path": "~/VoxeraOS/notes/report.txt",
            "destination_dir": "~/VoxeraOS/notes/receipts",
            "mode": "copy",
            "overwrite": False,
            "delete_original": False,
        },
    }
    result = normalize_preview_payload(payload)
    assert result["file_organize"]["source_path"] == "~/VoxeraOS/notes/report.txt"
    assert result["file_organize"]["mode"] == "copy"


def test_normalize_preview_payload_accepts_steps():
    payload = {
        "goal": "check if a.txt exists",
        "steps": [{"skill_id": "files.exists", "args": {"path": "~/VoxeraOS/notes/a.txt"}}],
    }
    result = normalize_preview_payload(payload)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["skill_id"] == "files.exists"


def test_normalize_preview_payload_rejects_empty_steps():
    import pytest

    with pytest.raises(ValueError, match="steps must be a non-empty list"):
        normalize_preview_payload({"goal": "test", "steps": []})


def test_normalize_preview_payload_rejects_invalid_file_organize():
    import pytest

    with pytest.raises(ValueError, match="file_organize.source_path is required"):
        normalize_preview_payload(
            {
                "goal": "test",
                "file_organize": {"source_path": "", "destination_dir": "x"},
            }
        )


# ---------------------------------------------------------------------------
# End-to-end: bounded file intent -> normalized preview
# ---------------------------------------------------------------------------


def test_end_to_end_exists_to_normalized_preview():
    draft = maybe_draft_job_payload("check if a.txt exists")
    assert draft is not None
    normalized = normalize_preview_payload(draft)
    assert normalized["goal"] == "check if a.txt exists in notes"
    assert normalized["steps"][0]["skill_id"] == "files.exists"


def test_end_to_end_copy_to_normalized_preview():
    draft = maybe_draft_job_payload("copy report.txt into receipts")
    assert draft is not None
    normalized = normalize_preview_payload(draft)
    assert "file_organize" in normalized
    assert normalized["file_organize"]["mode"] == "copy"
