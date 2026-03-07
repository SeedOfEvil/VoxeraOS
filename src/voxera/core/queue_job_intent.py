from __future__ import annotations

from typing import Any

from .queue_contracts import detect_request_kind


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


def build_queue_job_intent(
    payload: dict[str, Any],
    *,
    source_lane: str,
) -> dict[str, Any]:
    existing_raw = payload.get("job_intent")
    existing = existing_raw if isinstance(existing_raw, dict) else {}

    request_kind = _clean_text(existing.get("request_kind")) or detect_request_kind(payload)
    mission_id = _clean_text(
        existing.get("mission_id") or payload.get("mission_id") or payload.get("mission")
    )
    goal = _clean_text(existing.get("goal") or payload.get("goal") or payload.get("plan_goal"))
    title = _clean_text(existing.get("title") or payload.get("title") or payload.get("name"))
    notes = _clean_text(existing.get("notes") or payload.get("notes"))

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
        "expected_artifacts": _clean_list(
            existing.get("expected_artifacts") or payload.get("expected_artifacts")
        ),
        "operator_summary": _clean_text(
            existing.get("operator_summary")
            or payload.get("planner_summary")
            or payload.get("summary")
        ),
        "rationale": _clean_text(existing.get("rationale") or payload.get("rationale")),
        "planning_payload": _coerce_planning_payload(
            existing.get("planning_payload") or payload.get("planning_payload")
        ),
    }
    # Keep permissive + deterministic: prune nulls while preserving empty list fields.
    for key in ("mission_id", "title", "goal", "notes", "operator_summary", "rationale"):
        if intent.get(key) is None:
            intent.pop(key, None)
    if intent.get("planning_payload") is None:
        intent.pop("planning_payload", None)
    return intent


def enrich_queue_job_payload(payload: dict[str, Any], *, source_lane: str) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["job_intent"] = build_queue_job_intent(enriched, source_lane=source_lane)
    if "title" not in enriched and isinstance(enriched["job_intent"].get("title"), str):
        enriched["title"] = enriched["job_intent"]["title"]
    if "goal" not in enriched and isinstance(enriched["job_intent"].get("goal"), str):
        enriched["goal"] = enriched["job_intent"]["goal"]
    return enriched
