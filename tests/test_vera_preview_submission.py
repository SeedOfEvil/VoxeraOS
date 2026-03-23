from __future__ import annotations

import json

from voxera.vera import service as vera_service
from voxera.vera.preview_submission import (
    is_active_preview_submit_request,
    submit_active_preview_for_session,
)


def test_preview_submit_detection_preserves_save_as_boundary():
    assert is_active_preview_submit_request("save it")
    assert not is_active_preview_submit_request("save it as renamed.txt")


def test_submit_active_preview_without_preview_is_truthful(tmp_path):
    queue = tmp_path / "queue"
    session_id = "vera-test-missing-preview"

    message, status = submit_active_preview_for_session(
        queue_root=queue,
        session_id=session_id,
        preview=None,
    )

    assert status == "handoff_missing_preview"
    assert "did not submit anything" in message.lower()
    handoff = vera_service.read_session_handoff_state(queue, session_id)
    assert handoff is not None
    assert handoff["status"] == "missing_preview"
    assert vera_service.read_session_preview(queue, session_id) is None


def test_submit_active_preview_uses_authoritative_preview_and_clears_it(tmp_path):
    queue = tmp_path / "queue"
    session_id = "vera-test-submit-preview"
    preview = {
        "goal": "write a file called alberta.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/alberta.txt",
            "content": "Edmonton is the capital of Alberta.",
            "mode": "overwrite",
        },
    }
    seen_job_refs: list[str] = []
    vera_service.write_session_preview(queue, session_id, preview)

    message, status = submit_active_preview_for_session(
        queue_root=queue,
        session_id=session_id,
        preview=vera_service.read_session_preview(queue, session_id),
        register_linked_job=lambda root, sid, job_ref: seen_job_refs.append(
            f"{root.name}:{sid}:{job_ref}"
        ),
    )

    assert status == "handoff_submitted"
    assert "I submitted the job to VoxeraOS" in message
    assert vera_service.read_session_preview(queue, session_id) is None
    inbox_files = list((queue / "inbox").glob("inbox-*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["goal"] == preview["goal"]
    assert payload["write_file"] == preview["write_file"]
    handoff = vera_service.read_session_handoff_state(queue, session_id)
    assert handoff is not None
    assert handoff["status"] == "submitted"
    assert handoff["job_id"]
    assert seen_job_refs == [f"{queue.name}:{session_id}:inbox-{handoff['job_id']}.json"]
