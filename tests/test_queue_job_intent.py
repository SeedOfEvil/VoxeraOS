from __future__ import annotations

from voxera.core.queue_job_intent import build_queue_job_intent, enrich_queue_job_payload


def test_build_queue_job_intent_derives_fields_from_legacy_payload() -> None:
    intent = build_queue_job_intent(
        {
            "goal": "Summarize queue health",
            "steps": [{"skill": "system.status", "args": {}}],
        },
        source_lane="panel_queue_create",
    )

    assert intent["request_kind"] == "goal"
    assert intent["source_lane"] == "panel_queue_create"
    assert intent["goal"] == "Summarize queue health"
    assert intent["step_summaries"] == ["system.status"]


def test_build_queue_job_intent_preserves_existing_structured_hints() -> None:
    payload = {
        "mission_id": "system_check",
        "job_intent": {
            "request_kind": "mission_id",
            "source_lane": "mission_template",
            "candidate_skills": ["system.status"],
            "approval_hints": ["manual"],
            "planning_payload": {"planner": "cloud"},
        },
    }
    intent = build_queue_job_intent(payload, source_lane="queue_daemon")

    assert intent["request_kind"] == "mission_id"
    assert intent["source_lane"] == "mission_template"
    assert intent["candidate_skills"] == ["system.status"]
    assert intent["approval_hints"] == ["manual"]
    assert intent["planning_payload"] == {"planner": "cloud"}


def test_enrich_queue_job_payload_is_additive() -> None:
    payload = {"goal": "Run diagnostics"}
    enriched = enrich_queue_job_payload(payload, source_lane="inbox_cli")

    assert enriched["goal"] == "Run diagnostics"
    assert "job_intent" in enriched
    assert enriched["job_intent"]["goal"] == "Run diagnostics"
