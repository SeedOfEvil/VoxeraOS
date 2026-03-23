from voxera.vera.draft_revision import interpret_active_preview_draft_revision

_SAMPLE_PREVIEW = {
    "goal": "write a file called note-1774131870.txt with provided content",
    "write_file": {
        "path": "~/VoxeraOS/notes/note-1774131870.txt",
        "content": "2 + 2 is 4.",
        "mode": "overwrite",
    },
}


def test_interpret_active_preview_draft_revision_renames_note_preview():
    revision = interpret_active_preview_draft_revision(
        "call the note math.txt",
        _SAMPLE_PREVIEW,
    )

    assert revision is not None
    assert revision["goal"] == "write a file called math.txt with provided content"
    assert revision["write_file"] == {
        "path": "~/VoxeraOS/notes/math.txt",
        "content": "2 + 2 is 4.",
        "mode": "overwrite",
    }


def test_interpret_active_preview_draft_revision_updates_safe_explicit_path():
    preview = {
        "goal": "write a file called draft.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/draft.txt",
            "content": "Explain photosynthesis simply.",
            "mode": "append",
        },
    }

    revision = interpret_active_preview_draft_revision(
        "use path: ~/VoxeraOS/notes/photosynthesis.txt",
        preview,
    )

    assert revision is not None
    assert revision["write_file"] == {
        "path": "~/VoxeraOS/notes/photosynthesis.txt",
        "content": "Explain photosynthesis simply.",
        "mode": "append",
    }


def test_interpret_active_preview_draft_revision_rejects_unsafe_path():
    revision = interpret_active_preview_draft_revision(
        "use path: ~/VoxeraOS/notes/../bad.txt",
        _SAMPLE_PREVIEW,
    )

    assert revision is None


def test_interpret_active_preview_draft_revision_refines_content_in_place():
    preview = {
        "goal": "write a file called joke.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/joke.txt",
            "content": "Why did the developer go broke? Because they used up all their cache.",
            "mode": "append",
        },
    }

    revision = interpret_active_preview_draft_revision(
        'change the content to "A shorter joke"',
        preview,
    )

    assert revision is not None
    assert revision["write_file"] == {
        "path": "~/VoxeraOS/notes/joke.txt",
        "content": "A shorter joke",
        "mode": "append",
    }


def test_content_refinement_with_called_phrase_does_not_rename_target():
    preview = {
        "goal": "write a file called script.ps1 with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/script.ps1",
            "content": "",
            "mode": "overwrite",
        },
    }

    revision = interpret_active_preview_draft_revision(
        "add content to script.ps1 an Active Directory script that creates a user called Skibbidy",
        preview,
    )

    assert revision is not None
    assert revision["write_file"] == {
        "path": "~/VoxeraOS/notes/script.ps1",
        "content": "an Active Directory script that creates a user called Skibbidy",
        "mode": "overwrite",
    }


def test_interpret_active_preview_draft_revision_can_use_recent_artifact_content():
    revision = interpret_active_preview_draft_revision(
        "use that as the content",
        _SAMPLE_PREVIEW,
        assistant_artifacts=[
            {"content": "Saved artifact body", "artifact_type": "info"},
        ],
    )

    assert revision is not None
    assert revision["write_file"]["path"] == "~/VoxeraOS/notes/note-1774131870.txt"
    assert revision["write_file"]["content"] == "Saved artifact body"
    assert revision["write_file"]["mode"] == "overwrite"
