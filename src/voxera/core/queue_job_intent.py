from __future__ import annotations

from typing import Any

from .queue_contracts import detect_request_kind

_QUEUE_BASELINE_EXPECTED_ARTIFACTS = [
    "plan.json",
    "execution_envelope.json",
    "execution_result.json",
    "step_results.json",
]
_ASSISTANT_BASELINE_EXPECTED_ARTIFACTS = [
    "assistant_response.json",
    "execution_envelope.json",
    "execution_result.json",
    "step_results.json",
]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text:
            cleaned.append(text)
    return cleaned


def _clean_step_summaries(payload: dict[str, Any]) -> list[str]:
    summaries = _clean_list(payload.get("intended_step_summaries"))
    if summaries:
        return summaries
    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list):
        return []
    derived: list[str] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            continue
        skill = _clean_text(item.get("skill_id") or item.get("skill"))
        if skill:
            derived.append(skill)
    return derived


def _coerce_planning_payload(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _default_expected_artifacts(*, request_kind: str, source_lane: str) -> list[str]:
    normalized_request_kind = request_kind.strip()
    if normalized_request_kind == "assistant_question":
        return list(_ASSISTANT_BASELINE_EXPECTED_ARTIFACTS)
    if normalized_request_kind in {
        "mission_id",
        "goal",
        "inline_steps",
        "write_file",
        "file_organize",
    }:
        return list(_QUEUE_BASELINE_EXPECTED_ARTIFACTS)
    if source_lane.strip() in {"assistant_advisory", "panel_mission_prompt", "inbox_cli"}:
        if source_lane.strip() == "assistant_advisory":
            return list(_ASSISTANT_BASELINE_EXPECTED_ARTIFACTS)
        return list(_QUEUE_BASELINE_EXPECTED_ARTIFACTS)
    return []


def build_queue_job_intent(
    payload: dict[str, Any],
    *,
    source_lane: str,
) -> dict[str, Any]:
    existing_raw = payload.get("job_intent")
    existing = existing_raw if isinstance(existing_raw, dict) else {}

    request_kind = _clean_text(existing.get("request_kind")) or detect_request_kind(payload)
    expected_artifacts = _clean_list(
        existing.get("expected_artifacts") or payload.get("expected_artifacts")
    )
    if not expected_artifacts:
        expected_artifacts = _default_expected_artifacts(
            request_kind=request_kind,
            source_lane=_clean_text(existing.get("source_lane")) or source_lane,
        )
    mission_id = _clean_text(
        existing.get("mission_id") or payload.get("mission_id") or payload.get("mission")
    )
    goal = _clean_text(existing.get("goal") or payload.get("goal") or payload.get("plan_goal"))
    title = _clean_text(existing.get("title") or payload.get("title") or payload.get("name"))
    notes = _clean_text(existing.get("notes") or payload.get("notes"))
    file_organize_raw = payload.get("file_organize")
    file_organize = file_organize_raw if isinstance(file_organize_raw, dict) else None

    intent: dict[str, Any] = {
        "schema_version": 1,
        "request_kind": request_kind,
        "source_lane": _clean_text(existing.get("source_lane")) or source_lane,
        "mission_id": mission_id,
        "title": title,
        "goal": goal,
        "notes": notes,
        "step_summaries": _clean_step_summaries(payload),
        "candidate_skills": _clean_list(
            existing.get("candidate_skills") or payload.get("candidate_skills")
        ),
        "action_hints": _clean_list(existing.get("action_hints") or payload.get("action_hints")),
        "approval_hints": _clean_list(
            existing.get("approval_hints") or payload.get("approval_hints")
        ),
        "expected_artifacts": expected_artifacts,
        "operator_summary": _clean_text(
            existing.get("operator_summary")
            or payload.get("planner_summary")
            or payload.get("summary")
        ),
        "rationale": _clean_text(existing.get("rationale") or payload.get("rationale")),
        "planning_payload": _coerce_planning_payload(
            existing.get("planning_payload") or payload.get("planning_payload")
        ),
        "file_organize": {
            "source_path": _clean_text(file_organize.get("source_path")),
            "destination_dir": _clean_text(file_organize.get("destination_dir")),
            "mode": _clean_text(file_organize.get("mode")),
            "overwrite": file_organize.get("overwrite"),
            "delete_original": file_organize.get("delete_original"),
        }
        if file_organize is not None
        else None,
    }
    # Keep permissive + deterministic: prune nulls while preserving empty list fields.
    for key in (
        "mission_id",
        "title",
        "goal",
        "notes",
        "operator_summary",
        "rationale",
        "file_organize",
    ):
        if intent.get(key) is None:
            intent.pop(key, None)
    if intent.get("planning_payload") is None:
        intent.pop("planning_payload", None)
    return intent


def enrich_queue_job_payload(payload: dict[str, Any], *, source_lane: str) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["job_intent"] = build_queue_job_intent(enriched, source_lane=source_lane)
    if "expected_artifacts" not in enriched and isinstance(
        enriched["job_intent"].get("expected_artifacts"), list
    ):
        enriched["expected_artifacts"] = list(enriched["job_intent"]["expected_artifacts"])
    if "title" not in enriched and isinstance(enriched["job_intent"].get("title"), str):
        enriched["title"] = enriched["job_intent"]["title"]
    if "goal" not in enriched and isinstance(enriched["job_intent"].get("goal"), str):
        enriched["goal"] = enriched["job_intent"]["goal"]
    return enriched
