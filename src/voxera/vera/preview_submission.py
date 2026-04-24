from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.file_intent import is_safe_notes_path
from ..core.inbox import add_inbox_payload
from . import session_store
from .draft_revision import looks_like_preview_rename_or_save_as_request

_EXPLICIT_EMPTY_FILE_RE = re.compile(
    r"\b(empty|blank|touch|zero[- ]byte|zero\s+byte)\b",
    re.IGNORECASE,
)


def _is_explicit_empty_file_intent(goal: str) -> bool:
    """Return True when the goal explicitly requests an empty/blank file."""
    return bool(_EXPLICIT_EMPTY_FILE_RE.search(goal))


_ALLOWED_TOP_LEVEL_KEYS = {
    "goal",
    "title",
    "mission_id",
    "parent_job_id",
    "root_job_id",
    "orchestration_depth",
    "sequence_index",
    "lineage_role",
    "enqueue_child",
    "write_file",
    "file_organize",
    "steps",
}

_HANDOFF_PATTERNS = (
    r"\bhand\s+it\s+off\b",
    r"\bhandoff\b",
    r"\bsubmit\s+it\b",
    r"\bsubmit\s+to\s+voxeraos\b",
    r"\bsend\s+it\s+to\s+voxeraos\b",
    r"\bqueue\s+it\b",
    r"\benqueue\s+it\b",
    r"\bpush\s+it\s+through\b",
    r"\b(do\s+it|go\s+ahead|proceed)\b.*\b(voxeraos|submit|send|queue)?\b",
    r"\b(submit|send|hand\s+off)\b.*\b(job|request|it|this|queue|voxeraos|now|please)\b",
)

_ACTIVE_PREVIEW_SUBMIT_PATTERNS = (
    r"\byes\s+please\b",
    r"\byes\s+go\s+ahead\b",
    r"\bthat\s+looks\s+good\s+now\b",
    r"\buse\s+it\b",
    r"\buse\s+this\s+preview\b",
    r"\buse\s+the\s+current\s+preview\b",
    r"\bthis\s+preview\s+is\s+correct\b",
    r"\bokay\s+now\s+use\s+it\b",
    r"\bthat\s+json\s+is\s+right\b",
    r"\bsend\s+this\s+version\b",
    r"\bsubmit\s+this\s+one\b",
    r"\bgo\s+with\s+this\b",
    r"\bcreate\s+it\b",
    r"\bsave\s+it\b",
    r"\bsave\s+this\b",
    r"\blet'?s\s+save\s+(?:it|this|that)\b",
    r"\bwrite\s+(?:it|this|that)\s+to\s+(?:a\s+)?(?:file|disk)\b",
)

_NATURAL_CONFIRMATION_RE = re.compile(
    r"(?:yes(?:\s+please)?|yes\s+go\s+ahead|go\s+ahead|do\s+it|send\s+it|submit\s+it|hand\s+it\s+off)[.!?]*",
    re.IGNORECASE,
)

# Near-miss / typo-like submit phrases that resemble a real submit command
# but don't match any canonical pattern.  Used for fail-closed detection.
_NEAR_SUBMIT_RE = re.compile(
    r"^(?:"
    r"send\s+i+t|"  # "send iit", "send iiit", etc.
    r"sen\s+it|"  # "sen it"
    r"sned\s+it|"  # "sned it"
    r"sendit|"  # "sendit" (no space)
    r"submt\s+it|"  # "submt it"
    r"sumbit\s+it|"  # "sumbit it"
    r"sbumit\s+it|"  # "sbumit it"
    r"submitt?\s+i+t|"  # "submit iit"
    r"sedn\s+it|"  # "sedn it"
    r"send\s+ti"  # "send ti"
    r")[.!?]*$",
    re.IGNORECASE,
)


def is_near_miss_submit_phrase(message: str) -> bool:
    """Detect typo-like near-submit phrases that do NOT match any canonical
    submit pattern.  These must fail closed — no queue handoff, no fake
    submission claim."""
    normalized = message.strip().lower()
    if not normalized:
        return False
    # If it already matches a real submit pattern, it's not a near-miss.
    if is_preview_submission_request(message):
        return False
    return bool(_NEAR_SUBMIT_RE.fullmatch(normalized))


def is_explicit_handoff_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _HANDOFF_PATTERNS)


def is_active_preview_submit_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    if looks_like_preview_rename_or_save_as_request(normalized):
        return False
    return any(re.search(pattern, normalized) for pattern in _ACTIVE_PREVIEW_SUBMIT_PATTERNS)


def is_natural_preview_submission_confirmation(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return bool(_NATURAL_CONFIRMATION_RE.fullmatch(normalized))


def is_preview_submission_request(message: str) -> bool:
    return (
        is_natural_preview_submission_confirmation(message)
        or is_explicit_handoff_request(message)
        or is_active_preview_submit_request(message)
    )


def should_submit_active_preview(message: str, *, preview_available: bool) -> bool:
    if not preview_available:
        return False
    normalized = message.strip().lower()
    if looks_like_preview_rename_or_save_as_request(normalized):
        # Fail closed on mixed mutate+submit phrasing. A turn that appears to
        # rename/save-as should stay in preview-mutation mode, not submit mode.
        return False
    return is_explicit_handoff_request(message) or is_active_preview_submit_request(message)


def normalize_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in _ALLOWED_TOP_LEVEL_KEYS:
        if key in payload:
            cleaned[key] = payload[key]

    goal = str(cleaned.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    cleaned = {"goal": goal, **{k: v for k, v in cleaned.items() if k != "goal"}}

    if "mission_id" in cleaned:
        mission_id = str(cleaned["mission_id"]).strip()
        if mission_id:
            cleaned["mission_id"] = mission_id
        else:
            cleaned.pop("mission_id", None)

    if "title" in cleaned:
        title = str(cleaned["title"]).strip()
        if title:
            cleaned["title"] = title
        else:
            cleaned.pop("title", None)

    enqueue_child = cleaned.get("enqueue_child")
    if enqueue_child is not None:
        if not isinstance(enqueue_child, dict):
            raise ValueError("enqueue_child must be an object")
        child_goal = str(enqueue_child.get("goal") or "").strip()
        if not child_goal:
            raise ValueError("enqueue_child.goal is required")
        normalized_child: dict[str, Any] = {"goal": child_goal}
        child_title = str(enqueue_child.get("title") or "").strip()
        if child_title:
            normalized_child["title"] = child_title
        cleaned["enqueue_child"] = normalized_child

    write_file = cleaned.get("write_file")
    if write_file is not None:
        if not isinstance(write_file, dict):
            raise ValueError("write_file must be an object")
        path = str(write_file.get("path") or "").strip()
        if not path:
            raise ValueError("write_file.path is required")
        if not is_safe_notes_path(path):
            raise ValueError(
                "write_file.path must be within ~/VoxeraOS/notes/ "
                "and must not contain parent traversal or target the queue control-plane"
            )
        content = write_file.get("content")
        if not isinstance(content, str):
            raise ValueError("write_file.content must be a string")
        mode = str(write_file.get("mode") or "overwrite").strip().lower()
        if mode not in {"overwrite", "append"}:
            raise ValueError("write_file.mode must be overwrite or append")
        cleaned["write_file"] = {"path": path, "content": content, "mode": mode}

    file_organize = cleaned.get("file_organize")
    if file_organize is not None:
        if not isinstance(file_organize, dict):
            raise ValueError("file_organize must be an object")
        source_path = str(file_organize.get("source_path") or "").strip()
        if not source_path:
            raise ValueError("file_organize.source_path is required")
        destination_dir = str(file_organize.get("destination_dir") or "").strip()
        if not destination_dir:
            raise ValueError("file_organize.destination_dir is required")
        fo_mode = str(file_organize.get("mode") or "copy").strip().lower()
        if fo_mode not in {"copy", "move"}:
            raise ValueError("file_organize.mode must be copy or move")
        overwrite = file_organize.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ValueError("file_organize.overwrite must be a boolean")
        delete_original = file_organize.get("delete_original", False)
        if not isinstance(delete_original, bool):
            raise ValueError("file_organize.delete_original must be a boolean")
        cleaned["file_organize"] = {
            "source_path": source_path,
            "destination_dir": destination_dir,
            "mode": fo_mode,
            "overwrite": overwrite,
            "delete_original": delete_original,
        }

    steps = cleaned.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")
        validated_steps: list[dict[str, Any]] = []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"step {idx} must be an object")
            skill_id = str(step.get("skill_id") or "").strip()
            if not skill_id:
                raise ValueError(f"step {idx} requires a non-empty skill_id")
            args = step.get("args", {})
            if not isinstance(args, dict):
                raise ValueError(f"step {idx} args must be an object")
            validated_steps.append({"skill_id": skill_id, "args": args})
        cleaned["steps"] = validated_steps

    return cleaned


def submit_preview(*, queue_root: Path, payload: dict[str, Any]) -> dict[str, str]:
    created = add_inbox_payload(queue_root, payload, source_lane="vera_handoff")
    if not created.exists():
        raise RuntimeError(f"queue write was not confirmed at {created}")

    job_id = created.stem.removeprefix("inbox-")
    return {
        "job_id": job_id,
        "job_path": str(created),
        "queue_path": str(queue_root),
        "ack": (
            f"I submitted the job to VoxeraOS. Job id: {job_id}. "
            "The request is now in the queue. Execution has not completed yet. "
            "VoxeraOS will handle planning, policy/approval, execution, and evidence."
        ),
    }


def submit_active_preview_for_session(
    *,
    queue_root: Path,
    session_id: str,
    preview: dict[str, Any] | None,
    register_linked_job: Callable[[Path, str, str], None] | None = None,
    submit_preview_hook: Callable[..., dict[str, str]] = submit_preview,
) -> tuple[str, str]:
    canonical_preview = session_store.read_session_preview(queue_root, session_id)
    if canonical_preview is not None and preview is not None and preview != canonical_preview:
        session_store.write_session_handoff_state(
            queue_root,
            session_id,
            attempted=False,
            queue_path=str(queue_root),
            status="ambiguous_preview_state",
            error="Provided preview does not match canonical active preview state",
        )
        return (
            "I did not submit anything because the active preview state was ambiguous. "
            "Please review the current preview and submit again.",
            "handoff_ambiguous_preview_state",
        )
    preview_to_submit = canonical_preview if canonical_preview is not None else preview
    if preview_to_submit is None:
        session_store.write_session_handoff_state(
            queue_root,
            session_id,
            attempted=False,
            queue_path=str(queue_root),
            status="missing_preview",
            error="No prepared preview found",
        )
        return (
            "I don’t have a prepared preview in this session yet, so I did not submit anything to VoxeraOS.",
            "handoff_missing_preview",
        )

    # Fail closed when a write_file preview has empty content without an
    # explicit empty-file intent.  Submitting an empty file silently is a
    # trust violation — the user asked for content that never materialized.
    _preview_write_file = preview_to_submit.get("write_file")
    if isinstance(_preview_write_file, dict):
        _preview_content = str(_preview_write_file.get("content") or "").strip()
        _preview_goal = str(preview_to_submit.get("goal") or "")
        if not _preview_content and not _is_explicit_empty_file_intent(_preview_goal):
            session_store.write_session_handoff_state(
                queue_root,
                session_id,
                attempted=False,
                queue_path=str(queue_root),
                status="empty_content_blocked",
                error="write_file.content is empty without explicit empty-file intent",
            )
            return (
                "I did not submit the job because the write preview has no content. "
                "The file content is empty — please provide the content to write "
                "before submitting. If you meant to create an empty file, "
                "say so explicitly (e.g. “create an empty file called x.txt”).",
                "handoff_empty_content_blocked",
            )

    try:
        ack = submit_preview_hook(queue_root=queue_root, payload=preview_to_submit)
        job_id = str(ack.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("queue accepted payload but returned no job id")
        session_store.write_session_handoff_state(
            queue_root,
            session_id,
            attempted=True,
            queue_path=str(ack.get("queue_path") or queue_root),
            status="submitted",
            job_id=job_id,
        )
        if register_linked_job is not None:
            register_linked_job(queue_root, session_id, f"inbox-{job_id}.json")
        session_store.write_session_preview(queue_root, session_id, None)
        return str(ack["ack"]), "handoff_submitted"
    except Exception as exc:
        session_store.write_session_handoff_state(
            queue_root,
            session_id,
            attempted=True,
            queue_path=str(queue_root),
            status="submit_failed",
            error=str(exc),
        )
        return (
            "I could not submit that job to VoxeraOS, so nothing was queued. "
            f"Submission failed with: {exc}",
            "handoff_submit_failed",
        )
