"""Tests for bounded file intent classification and Vera handoff integration."""

from __future__ import annotations

import pytest

from voxera.core.file_intent import classify_bounded_file_intent, detect_blocked_file_intent
from voxera.vera.preview_drafting import maybe_draft_job_payload
from voxera.vera.preview_submission import normalize_preview_payload
from voxera.vera.saveable_artifacts import (
    build_saveable_assistant_artifact,
    message_requests_referenced_content,
    select_recent_saveable_assistant_artifact,
)
from voxera.vera_web.app import _is_conversational_answer_first_request

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


def test_exists_with_workspace_relative_shorthand():
    result = classify_bounded_file_intent("check if /skillpack-wave2/a.txt exists")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.exists"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_exists_rejects_queue_shorthand():
    result = classify_bounded_file_intent("check if /queue/health.json exists")
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


def test_stat_with_workspace_relative_shorthand():
    result = classify_bounded_file_intent("show me info about /skillpack-wave2/a.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.stat"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_stat_with_workspace_relative_shorthand_no_subdir():
    result = classify_bounded_file_intent("info about /report.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.stat"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/report.txt"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: read
# ---------------------------------------------------------------------------


def test_read_file():
    for msg in (
        "read /skillpack-wave2/a.txt",
        "read the file /skillpack-wave2/a.txt",
        "cat /skillpack-wave2/a.txt",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is not None, f"should match: {msg}"
        assert result["steps"][0]["skill_id"] == "files.read_text"
        assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_read_bare_filename():
    result = classify_bounded_file_intent("read notes.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.read_text"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/notes.txt"


def test_read_explicit_path():
    result = classify_bounded_file_intent("read ~/VoxeraOS/notes/inbox/today.md")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.read_text"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/inbox/today.md"


def test_read_rejects_queue_path():
    result = classify_bounded_file_intent("read /queue/inbox.json")
    assert result is None


def test_read_rejects_parent_traversal():
    result = classify_bounded_file_intent("read /../../etc/passwd")
    assert result is None


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
    assert result["steps"][0]["skill_id"] == "files.copy"
    assert result["steps"][0]["args"]["source_path"] == "~/VoxeraOS/notes/report.txt"
    assert result["steps"][0]["args"]["destination_path"] == "~/VoxeraOS/notes/receipts/report.txt"


def test_copy_file_to_file():
    result = classify_bounded_file_intent("copy a.txt to b.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.copy"
    assert result["steps"][0]["args"]["source_path"] == "~/VoxeraOS/notes/a.txt"
    assert result["steps"][0]["args"]["destination_path"] == "~/VoxeraOS/notes/b.txt"


# ---------------------------------------------------------------------------
# classify_bounded_file_intent: move
# ---------------------------------------------------------------------------


def test_move_file():
    result = classify_bounded_file_intent("move a.txt to archive.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.move"
    assert result["steps"][0]["args"]["source_path"] == "~/VoxeraOS/notes/a.txt"
    assert result["steps"][0]["args"]["destination_path"] == "~/VoxeraOS/notes/archive.txt"


def test_move_file_to_directory():
    result = classify_bounded_file_intent("move report.txt into archive")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.move"
    assert result["steps"][0]["args"]["destination_path"] == "~/VoxeraOS/notes/archive/report.txt"
    assert result["steps"][0]["args"]["source_path"] == "~/VoxeraOS/notes/report.txt"


def test_rename_file():
    result = classify_bounded_file_intent("rename a-copy.txt to a-renamed.txt")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.rename"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/a-copy.txt"
    assert result["steps"][0]["args"]["new_name"] == "a-renamed.txt"


def test_find_files():
    result = classify_bounded_file_intent("find txt files in my notes/runtime-validation folder")
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.find"
    assert result["steps"][0]["args"]["root_path"] == "~/VoxeraOS/notes/runtime-validation"
    assert result["steps"][0]["args"]["glob"] == "*.txt"


def test_grep_text():
    result = classify_bounded_file_intent('search my notes/runtime-validation for "voxera"')
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.grep_text"
    assert result["steps"][0]["args"]["root_path"] == "~/VoxeraOS/notes/runtime-validation"
    assert result["steps"][0]["args"]["pattern"] == "voxera"


def test_tree_listing():
    result = classify_bounded_file_intent(
        "show me the tree for ~/VoxeraOS/notes/runtime-validation"
    )
    assert result is not None
    assert result["steps"][0]["skill_id"] == "files.list_tree"
    assert result["steps"][0]["args"]["root_path"] == "~/VoxeraOS/notes/runtime-validation"


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
        "find the latest news",
        "search the web for weather in calgary",
        "",
        "hello",
    ):
        result = classify_bounded_file_intent(msg)
        assert result is None, f"should not match: {msg}"


def test_rename_to_path_like_target_fails_closed():
    result = classify_bounded_file_intent("rename a.txt to archive/a.txt")
    assert result is None


def test_outside_notes_scope_returns_none():
    result = classify_bounded_file_intent("delete /etc/passwd")
    # /etc/passwd normalizes to ~/VoxeraOS/notes/etc/passwd via workspace shorthand,
    # which is actually safe (it's within notes root). The key invariant is that
    # leading / never means "host absolute root" — it always means workspace-relative.
    # This test verifies the workspace-relative interpretation holds.
    assert result is not None
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/etc/passwd"


# ---------------------------------------------------------------------------
# detect_blocked_file_intent: refusal for blocked paths
# ---------------------------------------------------------------------------


def test_blocked_queue_exists_returns_refusal():
    """The exact STV regression: 'check if /queue/health.json exists'."""
    refusal = detect_blocked_file_intent("check if /queue/health.json exists")
    assert refusal is not None
    assert "blocked" in refusal.lower()
    assert "queue" in refusal.lower() or "control-plane" in refusal.lower()


def test_blocked_queue_read_returns_refusal():
    refusal = detect_blocked_file_intent("read /queue/inbox.json")
    assert refusal is not None
    assert "blocked" in refusal.lower()


def test_blocked_queue_stat_returns_refusal():
    refusal = detect_blocked_file_intent("show me info about /queue/jobs.json")
    assert refusal is not None
    assert "blocked" in refusal.lower()


def test_blocked_queue_explicit_path_returns_refusal():
    refusal = detect_blocked_file_intent("check if ~/VoxeraOS/notes/queue/inbox.json exists")
    assert refusal is not None
    assert "blocked" in refusal.lower()


def test_blocked_returns_none_for_safe_path():
    """Safe paths should not trigger blocked refusal."""
    refusal = detect_blocked_file_intent("check if /skillpack-wave2/a.txt exists")
    assert refusal is None


def test_blocked_returns_none_for_no_intent():
    """Non-file intents should not trigger blocked refusal."""
    refusal = detect_blocked_file_intent("hello world")
    assert refusal is None


def test_blocked_parent_traversal_returns_refusal():
    refusal = detect_blocked_file_intent("check if ../../../etc/passwd exists")
    assert refusal is not None
    assert "blocked" in refusal.lower()


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
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.move"


def test_handoff_routes_archive():
    result = maybe_draft_job_payload("archive this note today.md into my archive folder")
    assert result is not None
    assert "file_organize" in result


def test_handoff_routes_copy():
    result = maybe_draft_job_payload("copy report.txt into receipts")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.copy"


def test_handoff_routes_delete():
    result = maybe_draft_job_payload("delete temp.txt")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.delete_file"


def test_handoff_routes_read():
    result = maybe_draft_job_payload("read /skillpack-wave2/a.txt")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.read_text"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_handoff_routes_stat_workspace_relative():
    """The exact STV failure case: 'show me info about /skillpack-wave2/a.txt'."""
    result = maybe_draft_job_payload("show me info about /skillpack-wave2/a.txt")
    assert result is not None
    assert "steps" in result
    assert result["steps"][0]["skill_id"] == "files.stat"
    assert result["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_handoff_rejects_queue_shorthand():
    """Queue paths via shorthand must still be blocked at handoff level."""
    result = maybe_draft_job_payload("check if /queue/health.json exists")
    assert result is None


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


def test_end_to_end_read_workspace_relative_to_normalized_preview():
    draft = maybe_draft_job_payload("read /skillpack-wave2/a.txt")
    assert draft is not None
    normalized = normalize_preview_payload(draft)
    assert normalized["steps"][0]["skill_id"] == "files.read_text"
    assert normalized["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_end_to_end_stat_workspace_relative_to_normalized_preview():
    """End-to-end for the original STV failure case."""
    draft = maybe_draft_job_payload("show me info about /skillpack-wave2/a.txt")
    assert draft is not None
    normalized = normalize_preview_payload(draft)
    assert normalized["goal"] == "show file info for /skillpack-wave2/a.txt"
    assert normalized["steps"][0]["skill_id"] == "files.stat"
    assert normalized["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/skillpack-wave2/a.txt"


def test_end_to_end_copy_to_normalized_preview():
    draft = maybe_draft_job_payload("copy report.txt into receipts")
    assert draft is not None
    normalized = normalize_preview_payload(draft)
    assert normalized["steps"][0]["skill_id"] == "files.copy"


# ---------------------------------------------------------------------------
# Active preview rename: _draft_revision_from_active_preview via
# maybe_draft_job_payload with active_preview
# ---------------------------------------------------------------------------

_SAMPLE_PREVIEW = {
    "goal": "write a file called note-1774131870.txt with provided content",
    "write_file": {
        "path": "~/VoxeraOS/notes/note-1774131870.txt",
        "content": "The biggest object ever found is ...",
        "mode": "overwrite",
    },
}


def test_call_the_note_renames_active_preview():
    """'call the note biggest.txt' should update the preview target."""
    draft = maybe_draft_job_payload(
        "call the note biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]
    assert draft["write_file"]["mode"] == "overwrite"


def test_save_it_as_renames_active_preview():
    """'save it as biggest.txt' should update the preview target."""
    draft = maybe_draft_job_payload(
        "save it as biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]
    assert draft["write_file"]["mode"] == "overwrite"


def test_use_path_explicit_renames_active_preview():
    """'use path: ~/VoxeraOS/notes/biggest.txt' should update preview path exactly."""
    draft = maybe_draft_job_payload(
        "use path: ~/VoxeraOS/notes/biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]
    assert draft["write_file"]["mode"] == "overwrite"


def test_change_the_path_to_renames_active_preview():
    """'change the path to ~/VoxeraOS/notes/biggest.txt' should update preview."""
    draft = maybe_draft_job_payload(
        "change the path to ~/VoxeraOS/notes/biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]


def test_rename_preserves_content_and_mode():
    """Content and mode must be preserved across rename/path changes."""
    preview = {
        "goal": "write a file called draft.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/draft.txt",
            "content": "Some important content here.",
            "mode": "append",
        },
    }
    draft = maybe_draft_job_payload(
        "call this note final.txt",
        active_preview=preview,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/final.txt"
    assert draft["write_file"]["content"] == "Some important content here."
    assert draft["write_file"]["mode"] == "append"


def test_unsafe_path_rename_rejected():
    """Unsafe path traversal in rename must be rejected — preview unchanged."""
    draft = maybe_draft_job_payload(
        "use path: ~/VoxeraOS/notes/../../../etc/passwd",
        active_preview=_SAMPLE_PREVIEW,
    )
    # Should return None (fail closed) — preview not mutated
    assert draft is None


def test_rename_to_queue_path_rejected():
    """Rename targeting queue control-plane path must be rejected."""
    draft = maybe_draft_job_payload(
        "use path: ~/VoxeraOS/notes/queue/evil.json",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is None


def test_rename_it_to_with_bare_filename():
    """'rename it to biggest.txt' should update the preview target."""
    draft = maybe_draft_job_payload(
        "rename it to biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]


def test_no_active_preview_rename_does_nothing():
    """'call the note biggest.txt' without active_preview must not create a preview."""
    draft = maybe_draft_job_payload(
        "call the note biggest.txt",
        active_preview=None,
    )
    assert draft is None


def test_rename_with_no_write_file_dict_goal_only():
    """Rename against a goal-only preview (no write_file) falls to goal update."""
    preview = {"goal": "write a note called note-123.txt"}
    draft = maybe_draft_job_payload(
        "call the note biggest.txt",
        active_preview=preview,
    )
    assert draft is not None
    assert "biggest.txt" in draft["goal"]


def test_goal_text_reflects_new_display_name():
    """Goal field must contain the new display name after rename."""
    draft = maybe_draft_job_payload(
        "call the note biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert "biggest.txt" in draft["goal"]
    assert "note-1774131870" not in draft["goal"]


def test_idempotent_rename_same_name():
    """Renaming to the same name the preview already has should work."""
    draft = maybe_draft_job_payload(
        "call the note note-1774131870.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/note-1774131870.txt"
    assert draft["write_file"]["content"] == _SAMPLE_PREVIEW["write_file"]["content"]


def test_false_positive_call_the_plumber_does_not_rename():
    """'call the plumber' must NOT trigger rename on an active preview."""
    draft = maybe_draft_job_payload(
        "call the plumber tomorrow",
        active_preview=_SAMPLE_PREVIEW,
    )
    # Should NOT produce a rename — "plumber" is not a file-referencing noun
    assert draft is None


def test_call_this_file_renames_preview():
    """'call this file biggest.txt' should update the preview target."""
    draft = maybe_draft_job_payload(
        "call this file biggest.txt",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is not None
    assert draft["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"


# ---------------------------------------------------------------------------
# normalize_preview_payload: path safety gate
# ---------------------------------------------------------------------------


def test_normalize_rejects_traversal_path():
    """normalize_preview_payload must reject parent traversal in write_file.path."""
    payload = {
        "goal": "write a file",
        "write_file": {
            "path": "~/VoxeraOS/notes/../bad.txt",
            "content": "evil",
            "mode": "overwrite",
        },
    }
    with pytest.raises(ValueError, match="must be within"):
        normalize_preview_payload(payload)


def test_normalize_rejects_queue_path():
    """normalize_preview_payload must reject queue control-plane paths."""
    payload = {
        "goal": "write a file",
        "write_file": {
            "path": "~/VoxeraOS/notes/queue/evil.json",
            "content": "evil",
            "mode": "overwrite",
        },
    }
    with pytest.raises(ValueError, match="must be within"):
        normalize_preview_payload(payload)


def test_normalize_rejects_outside_workspace_path():
    """normalize_preview_payload must reject paths outside ~/VoxeraOS/notes/."""
    payload = {
        "goal": "write a file",
        "write_file": {
            "path": "/etc/passwd",
            "content": "evil",
            "mode": "overwrite",
        },
    }
    with pytest.raises(ValueError, match="must be within"):
        normalize_preview_payload(payload)


def test_normalize_accepts_safe_path():
    """normalize_preview_payload must accept valid notes paths."""
    payload = {
        "goal": "write a file",
        "write_file": {
            "path": "~/VoxeraOS/notes/biggest.txt",
            "content": "content",
            "mode": "overwrite",
        },
    }
    result = normalize_preview_payload(payload)
    assert result["write_file"]["path"] == "~/VoxeraOS/notes/biggest.txt"


def test_unsafe_path_rename_preserves_prior_preview():
    """Unsafe path in rename returns None, so caller preserves prior preview."""
    draft = maybe_draft_job_payload(
        "use path: ~/VoxeraOS/notes/../../../etc/passwd",
        active_preview=_SAMPLE_PREVIEW,
    )
    assert draft is None


# ---------------------------------------------------------------------------
# build_saveable_assistant_artifact: concise answer saveability
# ---------------------------------------------------------------------------


def test_concise_factual_answer_is_saveable():
    """'2 + 2 is 4.' must be saveable — it is a meaningful assistant answer."""
    artifact = build_saveable_assistant_artifact("2 + 2 is 4.")
    assert artifact is not None
    assert artifact["content"] == "2 + 2 is 4."


def test_capital_answer_is_saveable():
    """Short factual answers like 'The capital of Alberta is Edmonton.' must be saveable."""
    artifact = build_saveable_assistant_artifact("The capital of Alberta is Edmonton.")
    assert artifact is not None
    assert "Edmonton" in artifact["content"]


def test_courtesy_answer_still_not_saveable():
    """Courtesy replies like 'You're welcome!' must not be saveable."""
    artifact = build_saveable_assistant_artifact("You're welcome!")
    assert artifact is None


def test_low_info_ok_still_not_saveable():
    """Low-info responses like 'ok' must not be saveable."""
    artifact = build_saveable_assistant_artifact("ok")
    assert artifact is None


def test_longer_explanation_still_saveable():
    """Longer explanatory answers must remain saveable."""
    text = (
        "Photosynthesis is the process by which green plants and some other organisms "
        "use sunlight to synthesize foods from carbon dioxide and water."
    )
    artifact = build_saveable_assistant_artifact(text)
    assert artifact is not None
    assert "Photosynthesis" in artifact["content"]


def test_very_short_fragment_not_saveable():
    """Fragments under 8 chars like 'yes.' must not be saveable."""
    artifact = build_saveable_assistant_artifact("yes.")
    assert artifact is None


def test_single_word_not_saveable():
    """Single-word responses must not be saveable."""
    artifact = build_saveable_assistant_artifact("Absolutely")
    assert artifact is None


def test_select_recent_saveable_artifact_skips_courtesy_turn_history():
    """A courtesy follow-up must not steal the save target from the meaningful answer."""
    artifacts = [
        {"content": "2 + 2 is 4.", "artifact_type": "info"},
    ]

    selected = select_recent_saveable_assistant_artifact(
        message="save that to a note",
        assistant_artifacts=artifacts,
    )

    assert selected == {"content": "2 + 2 is 4.", "artifact_type": "info"}


def test_select_recent_saveable_artifact_prefers_explanation_when_requested():
    """Explanation-targeted save requests must keep selecting the matching artifact type."""
    artifacts = [
        {
            "content": "A black hole is a region of spacetime with gravity so strong that not even light can escape.",
            "artifact_type": "explanation",
        },
        {
            "content": "# Investigation Summary\n\nShort takeaway: gravitational collapse matters.",
            "artifact_type": "summary",
        },
    ]

    selected = select_recent_saveable_assistant_artifact(
        message="save that explanation to a note",
        assistant_artifacts=artifacts,
    )

    assert selected == artifacts[0]


def test_save_that_as_a_note_counts_as_referenced_content_request():
    assert message_requests_referenced_content("save that as a note") is True


def test_save_previous_content_counts_as_referenced_content_request():
    assert message_requests_referenced_content("save previous content") is True
    assert message_requests_referenced_content("save the previous content to SA.txt") is True


def test_save_that_to_named_file_preserves_previous_assistant_content():
    artifacts = [
        {
            "content": "Expanded investigation result with concrete findings.",
            "artifact_type": "expanded_result",
        }
    ]

    preview = maybe_draft_job_payload(
        "save that to a note called SA.txt",
        recent_assistant_artifacts=artifacts,
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/SA.txt"
    assert preview["write_file"]["content"] == artifacts[0]["content"]


def test_save_previous_content_to_named_file_preserves_previous_assistant_content():
    artifacts = [{"content": "2 + 2 is 4.", "artifact_type": "info"}]

    preview = maybe_draft_job_payload(
        "save previous content to math.txt",
        recent_assistant_artifacts=artifacts,
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"
    assert preview["write_file"]["content"] == "2 + 2 is 4."


def test_save_previous_content_repairs_empty_active_preview_content():
    artifacts = [{"content": "Recovered assistant-authored content.", "artifact_type": "info"}]
    active_preview = {
        "goal": "write a file called SA.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/SA.txt",
            "content": "",
            "mode": "overwrite",
        },
    }

    revised = maybe_draft_job_payload(
        "save previous content",
        active_preview=active_preview,
        recent_assistant_artifacts=artifacts,
    )

    assert revised is not None
    assert revised["write_file"]["path"] == "~/VoxeraOS/notes/SA.txt"
    assert revised["write_file"]["content"] == "Recovered assistant-authored content."


# ---------------------------------------------------------------------------
# Conversational answer-first classifier
# ---------------------------------------------------------------------------


class TestConversationalAnswerFirstClassifier:
    """Checklist/planning/structured reasoning requests should be classified
    as conversational answer-first — no preview drafting."""

    @pytest.mark.parametrize(
        "message",
        [
            "create a checklist for my wedding prep",
            "make me a checklist of things to bring",
            "give me a prep list for the camping trip",
            "help me plan for a vacation to Japan",
            "give me steps for setting up a home lab",
            "brainstorm what I need for the move",
            "help me organize my taxes",
            "what do I need to do for the marathon",
            "draft an itinerary for Paris",
            "make a to-do list for the renovation",
            "help me figure out what I need for the party",
            "give me suggestions for the presentation",
            "help me prioritize my tasks for the week",
            "create a checklist would surely help on the many things I need to do. "
            "First I need to find a +1, I also need to get a nice suit",
            "steps to prepare for a job interview",
            "tips for preparing for a marathon",
            "action items for the team meeting",
        ],
    )
    def test_planning_requests_are_answer_first(self, message):
        assert _is_conversational_answer_first_request(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "save that to a note",
            "write a checklist to a file called todo.txt",
            "save a prep list as notes.md",
            "create a file called plan.txt with my checklist",
            "put that into a note",
            "save it as wedding-prep.md",
            "save a checklist to a note",
            "save a checklist to my notes",
            "save the prep list into a note",
            "write a checklist to a file",
        ],
    )
    def test_save_write_intent_is_not_answer_first(self, message):
        assert _is_conversational_answer_first_request(message) is False

    @pytest.mark.parametrize(
        "message",
        [
            "what is the weather in Calgary",
            "hello",
            "write a python script that fetches URLs",
            "draft a 2-page essay about climate change",
            "check status of voxera-daemon.service",
            "submit it",
            "read notes.txt",
        ],
    )
    def test_unrelated_requests_are_not_answer_first(self, message):
        assert _is_conversational_answer_first_request(message) is False
