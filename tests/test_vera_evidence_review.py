"""Tests for evidence-aware follow-up draft generation (PR 164).

Covers:
- _classify_followup_intent intent classification
- draft_followup_preview from failure / deny / success evidence
- followup_preview_message formatting
- is_followup_preview_request extended phrase detection
- original_goal propagation from job artifacts
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.vera.evidence_review import (
    ReviewedJobEvidence,
    _classify_followup_intent,
    draft_followup_preview,
    followup_preview_message,
    is_followup_preview_request,
    review_job_outcome,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_evidence(
    *,
    state: str = "succeeded",
    original_goal: str = "",
    failure_summary: str = "",
    latest_summary: str = "",
    job_id: str = "job-test.json",
) -> ReviewedJobEvidence:
    return ReviewedJobEvidence(
        job_id=job_id,
        state=state,
        lifecycle_state=state,
        terminal_outcome=state,
        approval_status="none",
        latest_summary=latest_summary,
        failure_summary=failure_summary,
        child_summary=None,
        original_goal=original_goal,
    )


def _write_job(
    queue: Path, job_id: str, *, bucket: str = "done", goal: str = "test goal", **artifacts
) -> None:
    stem = Path(job_id).stem
    bucket_dir = queue / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / job_id).write_text(json.dumps({"goal": goal}), encoding="utf-8")
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    if "execution_result" in artifacts:
        (art / "execution_result.json").write_text(
            json.dumps(artifacts["execution_result"]), encoding="utf-8"
        )
    if "failed_sidecar" in artifacts:
        failed_dir = queue / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        (failed_dir / f"{stem}.error.json").write_text(
            json.dumps(artifacts["failed_sidecar"]), encoding="utf-8"
        )
    if "approval" in artifacts:
        approvals = queue / "pending" / "approvals"
        approvals.mkdir(parents=True, exist_ok=True)
        (approvals / f"{stem}.approval.json").write_text(
            json.dumps(artifacts["approval"]), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# is_followup_preview_request — extended phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # existing phrases
        "prepare the next step",
        "draft the next step",
        "prepare next step",
        "draft follow-up",
        "prepare a follow-up",
        # new correction phrases
        "draft the correction",
        "draft a correction",
        "fix it and try again",
        # new safer-version phrases
        "make a safer version",
        "make it safer",
        "prepare a safer alternative",
        "try a safer approach",
        "safer version please",
        "prepare a safer version of that",
        # new retry-different-target phrases
        "retry that with a different target",
        "retry with a different target",
        "do the same but on another file",
        "do that on another host",
        "same goal with a different target",
        # continuation
        "continue from that result",
        "revise that based on what happened",
    ],
)
def test_is_followup_preview_request_recognises_all_new_phrases(message: str):
    assert is_followup_preview_request(message), f"Expected True for: {message!r}"


# ---------------------------------------------------------------------------
# _classify_followup_intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("draft the correction", "correction"),
        ("fix it and try again", "correction"),
        ("draft a correction for that", "correction"),
        ("make a safer version", "safer_version"),
        ("make it safer please", "safer_version"),
        ("prepare a safer alternative", "safer_version"),
        ("safer version of that", "safer_version"),
        ("retry that with a different target", "retry_different_target"),
        ("retry with a different target", "retry_different_target"),
        ("do the same but on another path", "retry_different_target"),
        ("with a different target this time", "retry_different_target"),
        ("prepare the next step", "next_step"),
        ("draft follow-up", "next_step"),
        ("continue from that result", "next_step"),
        ("prepare a follow-up", "next_step"),
    ],
)
def test_classify_followup_intent(message: str, expected_intent: str):
    assert _classify_followup_intent(message) == expected_intent


# ---------------------------------------------------------------------------
# draft_followup_preview — next_step intent (default)
# ---------------------------------------------------------------------------


def test_draft_followup_from_success_uses_original_goal():
    ev = _make_evidence(
        state="succeeded",
        original_goal="open https://example.com",
        latest_summary="Page loaded successfully",
    )
    payload = draft_followup_preview(ev, user_message="prepare the next step")
    assert "open https://example.com" in payload["goal"]
    assert "next step" in payload["goal"].lower() or "prepare" in payload["goal"].lower()


def test_draft_followup_from_success_without_original_goal_uses_summary():
    ev = _make_evidence(
        state="succeeded",
        original_goal="",
        latest_summary="Read file complete",
    )
    payload = draft_followup_preview(ev, user_message="prepare the next step")
    goal = payload["goal"]
    assert goal  # must produce something
    assert "Read file complete" in goal or "next" in goal.lower()


def test_draft_followup_from_failure_next_step_includes_failure_context():
    ev = _make_evidence(
        state="failed",
        original_goal="read the file ~/secret.txt",
        failure_summary="permission denied",
    )
    payload = draft_followup_preview(ev, user_message="prepare the next step")
    goal = payload["goal"]
    assert "read the file ~/secret.txt" in goal
    assert "permission denied" in goal


def test_draft_followup_from_awaiting_approval_next_step_mentions_approval():
    ev = _make_evidence(
        state="awaiting_approval",
        original_goal="open https://restricted.internal",
        latest_summary="Requires operator approval",
    )
    payload = draft_followup_preview(ev, user_message="prepare the next step")
    goal = payload["goal"]
    assert "approval" in goal.lower() or "restricted.internal" in goal


# ---------------------------------------------------------------------------
# draft_followup_preview — correction intent
# ---------------------------------------------------------------------------


def test_draft_followup_correction_from_failure_includes_original_and_error():
    ev = _make_evidence(
        state="failed",
        original_goal="open https://bad-url.example",
        failure_summary="DNS resolution failed",
    )
    payload = draft_followup_preview(ev, user_message="draft the correction")
    goal = payload["goal"]
    assert "open https://bad-url.example" in goal
    assert "DNS resolution failed" in goal


def test_draft_followup_correction_without_original_goal_uses_failure_summary():
    ev = _make_evidence(
        state="failed",
        original_goal="",
        failure_summary="invalid path",
    )
    payload = draft_followup_preview(ev, user_message="fix it and try again")
    assert "invalid path" in payload["goal"]


def test_draft_followup_correction_without_failure_summary_retries_original():
    ev = _make_evidence(
        state="failed",
        original_goal="write a note called test.txt",
        failure_summary="",
        latest_summary="",
    )
    payload = draft_followup_preview(ev, user_message="draft a correction for that")
    assert "write a note called test.txt" in payload["goal"]


# ---------------------------------------------------------------------------
# draft_followup_preview — safer_version intent
# ---------------------------------------------------------------------------


def test_draft_followup_safer_version_from_deny_includes_original_and_reason():
    ev = _make_evidence(
        state="awaiting_approval",
        original_goal="open https://restricted.internal",
        latest_summary="requires approval for external access",
        failure_summary="",
    )
    payload = draft_followup_preview(ev, user_message="make a safer version")
    goal = payload["goal"]
    assert "open https://restricted.internal" in goal
    assert "safer" in goal.lower()


def test_draft_followup_safer_version_from_failure_includes_failure_context():
    ev = _make_evidence(
        state="failed",
        original_goal="write a file called /etc/cron.d/myjob",
        failure_summary="permission denied: /etc/cron.d",
    )
    payload = draft_followup_preview(ev, user_message="make a safer version")
    goal = payload["goal"]
    assert "/etc/cron.d/myjob" in goal
    assert "safer" in goal.lower()
    assert "permission denied" in goal


def test_draft_followup_safer_version_without_original_goal():
    ev = _make_evidence(state="failed", original_goal="")
    payload = draft_followup_preview(ev, user_message="safer alternative please")
    assert payload["goal"]  # must produce something non-empty


# ---------------------------------------------------------------------------
# draft_followup_preview — retry_different_target intent
# ---------------------------------------------------------------------------


def test_draft_followup_retry_different_target_preserves_original_goal():
    ev = _make_evidence(
        state="succeeded",
        original_goal="open https://old.example.com",
    )
    payload = draft_followup_preview(ev, user_message="retry that with a different target")
    # Original goal is preserved as the starting draft; user refines the target
    assert "open https://old.example.com" in payload["goal"]


def test_draft_followup_retry_different_target_without_original_goal():
    ev = _make_evidence(state="succeeded", original_goal="")
    payload = draft_followup_preview(ev, user_message="retry with a different target")
    assert payload["goal"]  # must produce something


def test_draft_followup_retry_different_target_file_action():
    ev = _make_evidence(
        state="failed",
        original_goal="read the file ~/notes/old.txt",
        failure_summary="file not found",
    )
    payload = draft_followup_preview(ev, user_message="do the same but on another file")
    # Intent is retry-different-target so original goal is preserved for user to edit
    assert "read the file ~/notes/old.txt" in payload["goal"]


# ---------------------------------------------------------------------------
# followup_preview_message
# ---------------------------------------------------------------------------


def test_followup_preview_message_contains_job_id_and_state():
    ev = _make_evidence(
        state="failed",
        original_goal="open https://example.com",
        failure_summary="DNS error",
        job_id="job-test.json",
    )
    payload = {"goal": "open https://example.com (correcting: DNS error)"}
    msg = followup_preview_message(ev, payload, user_message="draft the correction")
    assert "job-test.json" in msg
    assert "`failed`" in msg
    assert "did not submit anything" in msg.lower()


def test_followup_preview_message_json_is_valid():
    ev = _make_evidence(state="succeeded", original_goal="open https://example.com")
    payload = {"goal": "prepare the next step after: open https://example.com"}
    msg = followup_preview_message(ev, payload, user_message="prepare the next step")
    # Extract json block
    import re

    m = re.search(r"```json\n([\s\S]*?)\n```", msg)
    assert m is not None, "Expected a ```json block in followup message"
    parsed = json.loads(m.group(1))
    assert parsed == payload


def test_followup_preview_message_retry_different_target_advises_update():
    ev = _make_evidence(
        state="succeeded",
        original_goal="open https://old.example.com",
    )
    payload = {"goal": "open https://old.example.com"}
    msg = followup_preview_message(ev, payload, user_message="retry with a different target")
    assert "update the target" in msg.lower() or "previous request" in msg.lower()


def test_followup_preview_message_failure_includes_failure_reason():
    ev = _make_evidence(
        state="failed",
        original_goal="read the file ~/test.txt",
        failure_summary="file not found",
    )
    payload = {"goal": "retry: read the file ~/test.txt after addressing: file not found"}
    msg = followup_preview_message(ev, payload, user_message="prepare the next step")
    assert "file not found" in msg


def test_followup_preview_message_awaiting_approval_mentions_approval():
    ev = _make_evidence(
        state="awaiting_approval",
        original_goal="open https://restricted.internal",
    )
    payload = {"goal": "review approval requirements for: open https://restricted.internal"}
    msg = followup_preview_message(ev, payload, user_message="prepare the next step")
    assert "approval" in msg.lower()


# ---------------------------------------------------------------------------
# original_goal propagated from canonical job artifact
# ---------------------------------------------------------------------------


def test_review_job_outcome_reads_original_goal(tmp_path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        "job-goal-test.json",
        bucket="done",
        goal="open https://original.example.com",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "step_results": [{"step_index": 1, "status": "succeeded", "summary": "Done"}],
        },
    )
    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-goal-test.json")
    assert evidence is not None
    assert evidence.original_goal == "open https://original.example.com"


def test_review_job_outcome_original_goal_empty_for_no_goal_field(tmp_path):
    queue = tmp_path / "queue"
    # Write job without a goal field
    stem = "job-no-goal"
    job_id = f"{stem}.json"
    bucket_dir = queue / "done"
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / job_id).write_text(json.dumps({"title": "no goal here"}), encoding="utf-8")
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)

    evidence = review_job_outcome(queue_root=queue, requested_job_id=job_id)
    assert evidence is not None
    assert evidence.original_goal == ""


# ---------------------------------------------------------------------------
# No regression: preview lifecycle is not broken by new follow-up logic
# ---------------------------------------------------------------------------


def test_draft_followup_does_not_auto_submit_returns_dict():
    """draft_followup_preview must always return a plain dict, never submit."""
    for state in ("succeeded", "failed", "awaiting_approval", "canceled", "pending"):
        ev = _make_evidence(state=state, original_goal="open https://example.com")
        result = draft_followup_preview(ev, user_message="prepare the next step")
        assert isinstance(result, dict)
        assert "goal" in result
        assert isinstance(result["goal"], str)
        assert result["goal"]  # non-empty


def test_draft_followup_goal_is_always_string_across_intents():
    ev = _make_evidence(
        state="failed",
        original_goal="read the file ~/notes/file.txt",
        failure_summary="file not found",
    )
    for msg in [
        "prepare the next step",
        "draft the correction",
        "make a safer version",
        "retry that with a different target",
        "",
    ]:
        result = draft_followup_preview(ev, user_message=msg)
        assert isinstance(result.get("goal"), str), f"goal must be str for message={msg!r}"
        assert result["goal"], f"goal must be non-empty for message={msg!r}"
