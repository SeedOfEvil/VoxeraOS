from __future__ import annotations

import json

from voxera.core import queue_assistant
from voxera.core.missions import MissionStep, MissionTemplate
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig, RunResult


def _force_policy_ask(monkeypatch) -> None:
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def _stub_plan(monkeypatch, *, mission_id: str = "cloud_planned") -> None:
    async def _fake_plan(goal, cfg, registry, source="cli", job_ref=None, **_kwargs):
        return MissionTemplate(
            id=mission_id,
            title="Stub Plan",
            goal=goal,
            steps=[MissionStep(skill_id="system.status", args={})],
            notes="stub",
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _fake_plan)


def test_execution_envelope_normalizes_goal_job(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-goal.json").write_text(json.dumps({"goal": "health"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    envelope = json.loads(
        (queue_root / "artifacts" / "job-goal" / "execution_envelope.json").read_text(
            encoding="utf-8"
        )
    )
    assert envelope["job"]["request_kind"] == "goal"
    assert envelope["request"]["job_intent"]["source_lane"] == "queue_daemon"
    assert envelope["execution"]["steps"][0]["skill_id"] == "system.status"
    job_intent_artifact = json.loads(
        (queue_root / "artifacts" / "job-goal" / "job_intent.json").read_text(encoding="utf-8")
    )
    assert job_intent_artifact["request_kind"] == "goal"


def test_execution_envelope_normalizes_inline_steps_job(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-inline.json").write_text(
        json.dumps({"steps": [{"skill": "system.status", "args": {}}], "title": "Inline"})
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    envelope = json.loads(
        (queue_root / "artifacts" / "job-inline" / "execution_envelope.json").read_text(
            encoding="utf-8"
        )
    )
    assert envelope["job"]["request_kind"] == "inline_steps"
    assert envelope["request"]["title"] == "Inline"


def test_lineage_metadata_carried_into_execution_artifacts(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-lineage.json").write_text(
        json.dumps(
            {
                "goal": "health",
                "parent_job_id": "parent-demo.json",
                "root_job_id": "root-demo.json",
                "orchestration_depth": 1,
                "sequence_index": 2,
                "lineage_role": "child",
            }
        )
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    art = queue_root / "artifacts" / "job-lineage"
    envelope = json.loads((art / "execution_envelope.json").read_text(encoding="utf-8"))
    result = json.loads((art / "execution_result.json").read_text(encoding="utf-8"))
    plan = json.loads((art / "plan.json").read_text(encoding="utf-8"))

    assert envelope["job"]["lineage"]["parent_job_id"] == "parent-demo.json"
    assert envelope["job"]["lineage"]["orchestration_depth"] == 1
    assert result["lineage"]["root_job_id"] == "root-demo.json"
    assert result["lineage"]["sequence_index"] == 2
    assert plan["lineage"]["lineage_role"] == "child"


def test_malformed_lineage_metadata_is_safely_sanitized(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-lineage-bad.json").write_text(
        json.dumps(
            {
                "goal": "health",
                "parent_job_id": "  ",
                "root_job_id": 7,
                "orchestration_depth": "bad",
                "sequence_index": -4,
                "lineage_role": "invalid",
            }
        )
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    result = json.loads(
        (queue_root / "artifacts" / "job-lineage-bad" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["lineage"] == {
        "parent_job_id": None,
        "root_job_id": None,
        "orchestration_depth": 0,
        "sequence_index": None,
        "lineage_role": None,
    }


def test_step_results_and_execution_result_written_for_failure(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-fail.json").write_text(json.dumps({"goal": "fail please"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _fail_run(*args, **kwargs):
        return RunResult(
            ok=False,
            error="planned failure",
            data={
                "results": [
                    {
                        "step": 1,
                        "skill": "system.status",
                        "args": {},
                        "ok": False,
                        "output": "",
                        "error": "planned failure",
                        "started_at_ms": 1,
                        "finished_at_ms": 2,
                        "duration_ms": 1,
                    }
                ],
                "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "failed"}],
                "lifecycle_state": "step_failed",
                "terminal_outcome": "failed",
                "current_step_index": 1,
                "last_completed_step": 0,
                "last_attempted_step": 1,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _fail_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-fail" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is False
    assert execution_result["terminal_outcome"] == "failed"

    step_results = json.loads(
        (queue_root / "artifacts" / "job-fail" / "step_results.json").read_text(encoding="utf-8")
    )
    assert step_results[0]["status"] == "failed"
    assert step_results[0]["duration_ms"] == 1


def test_assistant_queue_writes_structured_execution_artifacts(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": "queued answer",
        "provider": "primary",
        "model": "mock",
        "fallback_used": False,
        "fallback_reason": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-assistant.json").write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": "What changed?",
                "thread_id": "t-1",
                "advisory": True,
                "read_only": True,
                "action_hints": ["assistant.advisory"],
            }
        )
    )

    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-assistant" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    execution_envelope = json.loads(
        (queue_root / "artifacts" / "job-assistant" / "execution_envelope.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is True
    assert execution_result["execution_lane"] == "fast_read_only"
    assert execution_result["fast_lane"]["used"] is True
    assert execution_result["step_results"][0]["skill_id"] == "assistant.advisory"

    assistant_artifact = json.loads(
        (queue_root / "artifacts" / "job-assistant" / "assistant_response.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_envelope["execution"]["mode"] == "assistant_advisory"
    assert execution_envelope["execution"]["lane"] == "fast_read_only"
    assert (
        execution_envelope["execution"]["fast_lane"]["eligibility_reason"]
        == "eligible_read_only_assistant_advisory"
    )
    assert execution_envelope["job"]["request_kind"] == "assistant_question"
    assert assistant_artifact["execution_lane"] == execution_result["execution_lane"]
    assert (
        assistant_artifact["fast_lane"]
        == execution_result["fast_lane"]
        == execution_envelope["execution"]["fast_lane"]
    )


def test_assistant_non_eligible_payload_falls_back_to_normal_queue_lane(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": "queued answer",
        "provider": "primary",
        "model": "mock",
        "fallback_used": False,
        "fallback_reason": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-assistant-non-eligible.json").write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": "What changed?",
                "thread_id": "t-2",
                "advisory": True,
                "read_only": True,
                "action_hints": ["assistant.advisory", "files.read_text"],
            }
        )
    )

    daemon.process_pending_once()

    execution_result = json.loads(
        (
            queue_root / "artifacts" / "job-assistant-non-eligible" / "execution_result.json"
        ).read_text(encoding="utf-8")
    )
    execution_envelope = json.loads(
        (
            queue_root / "artifacts" / "job-assistant-non-eligible" / "execution_envelope.json"
        ).read_text(encoding="utf-8")
    )
    assistant_artifact = json.loads(
        (
            queue_root / "artifacts" / "job-assistant-non-eligible" / "assistant_response.json"
        ).read_text(encoding="utf-8")
    )
    assert execution_result["execution_lane"] == "queue"
    assert execution_result["fast_lane"]["used"] is False
    assert execution_result["fast_lane"]["eligibility_reason"] == "action_hints_not_eligible"
    assert execution_envelope["execution"]["lane"] == "queue"
    assert execution_envelope["execution"]["fast_lane"]["used"] is False
    assert assistant_artifact["execution_lane"] == execution_result["execution_lane"]
    assert (
        assistant_artifact["fast_lane"]
        == execution_result["fast_lane"]
        == execution_envelope["execution"]["fast_lane"]
    )


def test_assistant_approval_flag_disables_fast_lane(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": "queued answer",
        "provider": "primary",
        "model": "mock",
        "fallback_used": False,
        "fallback_reason": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-assistant-approval.json").write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": "What changed?",
                "thread_id": "t-3",
                "advisory": True,
                "read_only": True,
                "approval_required": True,
                "action_hints": ["assistant.advisory"],
            }
        )
    )

    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-assistant-approval" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["execution_lane"] == "queue"
    assert execution_result["fast_lane"]["eligibility_reason"] == "approval_required"


def test_assistant_request_kind_in_job_intent_routes_to_assistant_lane(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": "queued answer",
        "provider": "primary",
        "model": "mock",
        "fallback_used": False,
        "fallback_reason": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-assistant-intent-only.json").write_text(
        json.dumps(
            {
                "question": "What changed?",
                "thread_id": "t-4",
                "advisory": True,
                "read_only": True,
                "action_hints": ["assistant.advisory"],
                "job_intent": {"request_kind": "assistant_question"},
            }
        )
    )

    daemon.process_pending_once()

    assert (queue_root / "done" / "job-assistant-intent-only.json").exists()
    assert not (queue_root / "failed" / "job-assistant-intent-only.json").exists()
    execution_result = json.loads(
        (
            queue_root / "artifacts" / "job-assistant-intent-only" / "execution_result.json"
        ).read_text(encoding="utf-8")
    )
    assert execution_result["ok"] is True
    assert execution_result["terminal_outcome"] == "succeeded"
    assert execution_result["execution_lane"] == "fast_read_only"


def test_assistant_mutating_action_hint_is_not_fast_lane_eligible():
    eligible, reason = queue_assistant.evaluate_assistant_fast_lane_eligibility(
        {
            "kind": "assistant_question",
            "advisory": True,
            "read_only": True,
            "action_hints": ["clipboard.copy"],
        },
        request_kind="assistant_question",
    )
    assert eligible is False
    assert reason == "action_hints_not_eligible"


def test_assistant_malformed_payload_is_not_fast_lane_eligible():
    eligible, reason = queue_assistant.evaluate_assistant_fast_lane_eligibility(
        {
            "kind": "assistant_question",
            "advisory": True,
            "read_only": "yes",
            "action_hints": ["assistant.advisory"],
        },
        request_kind="assistant_question",
    )
    assert eligible is False
    assert reason == "read_only_flag_missing"


def test_step_results_include_structured_skill_result_fields(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-structured.json").write_text(json.dumps({"goal": "structured"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _fail_run(*args, **kwargs):
        return RunResult(
            ok=False,
            error="planned failure",
            data={
                "results": [
                    {
                        "step": 1,
                        "skill": "system.status",
                        "args": {},
                        "ok": False,
                        "output": "",
                        "error": "planned failure",
                        "started_at_ms": 1,
                        "finished_at_ms": 2,
                        "duration_ms": 1,
                        "summary": "deterministic summary",
                        "machine_payload": {"stable": True},
                        "output_artifacts": ["artifact.txt"],
                        "operator_note": "operator message",
                        "next_action_hint": "retry_after_fix",
                        "retryable": True,
                        "error_class": "runner_error",
                    }
                ],
                "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "failed"}],
                "lifecycle_state": "step_failed",
                "terminal_outcome": "failed",
                "current_step_index": 1,
                "last_completed_step": 0,
                "last_attempted_step": 1,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _fail_run  # type: ignore[method-assign]
    daemon.process_pending_once()

    step_results = json.loads(
        (queue_root / "artifacts" / "job-structured" / "step_results.json").read_text(
            encoding="utf-8"
        )
    )
    step = step_results[0]
    assert step["summary"] == "deterministic summary"
    assert step["machine_payload"] == {"stable": True}
    assert step["output_artifacts"] == ["artifact.txt"]
    assert step["operator_note"] == "operator message"
    assert step["next_action_hint"] == "retry_after_fix"
    assert step["retryable"] is True
    assert step["error_class"] == "runner_error"


def test_runtime_capability_block_halts_subsequent_steps_and_writes_artifacts(
    tmp_path, monkeypatch
):
    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-cap-block.json").write_text(
        json.dumps(
            {
                "title": "capability gate",
                "steps": [
                    {"skill_id": "system.status", "args": {}},
                    {"skill_id": "system.open_app", "args": {"name": "terminal"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    status_manifest = daemon.mission_runner.skill_runner.registry.get("system.status")

    original_get = daemon.mission_runner.skill_runner.registry.get

    def _patched_get(skill_id: str):
        if skill_id == "system.status":
            return status_manifest.model_copy(update={"capabilities": []})
        return original_get(skill_id)

    daemon.mission_runner.skill_runner.registry.get = _patched_get  # type: ignore[method-assign]

    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-cap-block.json").exists()

    step_results = json.loads(
        (queue_root / "artifacts" / "job-cap-block" / "step_results.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(step_results) == 1
    assert step_results[0]["skill_id"] == "system.status"
    assert step_results[0]["status"] == "blocked"
    assert step_results[0]["error_class"] == "missing_capability_metadata"
    assert step_results[0]["machine_payload"]["required_capabilities"] == []

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-cap-block" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["terminal_outcome"] == "blocked"
    assert execution_result["last_attempted_step"] == 1
    assert execution_result["step_results"][0]["next_action_hint"] == "fix_skill_manifest"


def test_retryable_failure_triggers_single_bounded_replan(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-replan.json").write_text(json.dumps({"goal": "retry once"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.max_replan_attempts = 1

    run_calls = {"n": 0}

    def _run(*args, **kwargs):
        run_calls["n"] += 1
        if run_calls["n"] == 1:
            return RunResult(
                ok=False,
                error="temporary",
                data={
                    "results": [
                        {
                            "step": 1,
                            "skill": "system.status",
                            "args": {},
                            "ok": False,
                            "error": "temporary",
                            "retryable": True,
                        }
                    ],
                    "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "failed"}],
                    "lifecycle_state": "step_failed",
                    "terminal_outcome": "failed",
                    "current_step_index": 1,
                    "last_completed_step": 0,
                    "last_attempted_step": 1,
                    "total_steps": 1,
                },
            )
        return RunResult(
            ok=True,
            output="ok",
            data={
                "results": [
                    {"step": 1, "skill": "system.status", "args": {}, "ok": True, "output": "ok"}
                ],
                "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "succeeded"}],
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": 1,
                "last_completed_step": 1,
                "last_attempted_step": 1,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _run  # type: ignore[method-assign]
    daemon.process_pending_once()

    assert run_calls["n"] == 2
    execution_result = json.loads(
        (queue_root / "artifacts" / "job-replan" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is True
    assert execution_result["attempt_index"] == 2
    assert execution_result["replan_count"] == 1
    assert execution_result["stop_reason"] == "succeeded"
    assert (queue_root / "artifacts" / "job-replan" / "plan.attempt-1.json").exists()
    assert (queue_root / "artifacts" / "job-replan" / "plan.attempt-2.json").exists()


def test_retryable_failure_stops_when_max_replans_reached(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-max-replan.json").write_text(json.dumps({"goal": "retry stops"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.max_replan_attempts = 1

    def _always_retryable(*args, **kwargs):
        return RunResult(
            ok=False,
            error="still bad",
            data={
                "results": [
                    {
                        "step": 1,
                        "skill": "system.status",
                        "args": {},
                        "ok": False,
                        "error": "still bad",
                        "retryable": True,
                    }
                ],
                "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "failed"}],
                "lifecycle_state": "step_failed",
                "terminal_outcome": "failed",
                "current_step_index": 1,
                "last_completed_step": 0,
                "last_attempted_step": 1,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _always_retryable  # type: ignore[method-assign]
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-max-replan" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is False
    assert execution_result["attempt_index"] == 2
    assert execution_result["replan_count"] == 1
    assert execution_result["evaluation_class"] == "retryable_failure"
    assert execution_result["stop_reason"] == "terminal_failure"


def test_approval_outcome_records_attempt_without_replan(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-approval.json").write_text(json.dumps({"goal": "needs approval"}))

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _pending(*args, **kwargs):
        return RunResult(
            ok=False,
            error="Mission paused for approval.",
            data={
                "status": "pending_approval",
                "step": 1,
                "skill": "system.set_volume",
                "results": [],
                "step_outcomes": [],
                "lifecycle_state": "awaiting_approval",
                "terminal_outcome": None,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _pending  # type: ignore[method-assign]
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-approval" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["terminal_outcome"] == "awaiting_approval"
    assert execution_result["attempt_index"] == 1
    assert execution_result["replan_count"] == 0
    assert execution_result["evaluation_class"] == "awaiting_approval"


def test_policy_block_records_non_retryable_evaluation(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-blocked.json").write_text(
        json.dumps({"steps": [{"skill_id": "system.status", "args": {}}]})
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _blocked(*args, **kwargs):
        return RunResult(
            ok=False,
            error="Denied by policy",
            data={
                "results": [
                    {
                        "step": 1,
                        "skill": "system.status",
                        "args": {},
                        "ok": False,
                        "error": "Denied by policy",
                    }
                ],
                "step_outcomes": [{"step": 1, "skill": "system.status", "outcome": "blocked"}],
                "lifecycle_state": "blocked",
                "terminal_outcome": "blocked",
                "current_step_index": 1,
                "last_completed_step": 0,
                "last_attempted_step": 1,
                "total_steps": 1,
            },
        )

    daemon.mission_runner.run = _blocked  # type: ignore[method-assign]
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-blocked" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["terminal_outcome"] == "blocked"
    assert execution_result["evaluation_class"] == "blocked_non_retryable"
    assert execution_result["replan_count"] == 0


def test_goal_unknown_skill_planning_failure_replans_once_and_keeps_artifacts(
    tmp_path, monkeypatch
):
    from voxera.core.mission_planner import MissionPlannerError

    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-goal-unknown-skill.json").write_text(
        json.dumps({"goal": "open an app and report status"}), encoding="utf-8"
    )

    calls = {"n": 0}

    async def _plan_with_first_unknown(goal, cfg, registry, source="queue", job_ref=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MissionPlannerError("Planner referenced unknown skill: system.not_a_real_skill")
        return MissionTemplate(
            id="cloud_planned",
            title="Fallback plan",
            goal=goal,
            steps=[MissionStep(skill_id="system.status", args={})],
            notes="replanned",
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _plan_with_first_unknown)

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.max_replan_attempts = 1
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-goal-unknown-skill" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is True
    assert execution_result["attempt_index"] == 2
    assert execution_result["replan_count"] == 1
    assert execution_result["max_replans"] == 1
    assert execution_result["stop_reason"] == "succeeded"

    plan_1 = json.loads(
        (queue_root / "artifacts" / "job-goal-unknown-skill" / "plan.attempt-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan_1["planning_error"]["evaluation_reason"] == "skill_not_found"
    assert (queue_root / "artifacts" / "job-goal-unknown-skill" / "plan.attempt-2.json").exists()


def test_inline_unknown_skill_fails_structured_without_daemon_crash(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-inline-unknown-skill.json").write_text(
        json.dumps({"steps": [{"skill_id": "system.not_a_real_skill", "args": {}}]}),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-inline-unknown-skill" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is False
    assert execution_result["terminal_outcome"] == "failed"
    assert execution_result["step_results"][0]["error_class"] == "skill_not_found"
    assert execution_result["attempt_index"] == 1
    assert execution_result["replan_count"] == 0


def test_goal_plus_inline_unknown_skill_triggers_one_bounded_replan(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-goal-inline-unknown.json").write_text(
        json.dumps(
            {
                "goal": "PR141 should do exactly one bounded replan on skill mismatch",
                "steps": [{"skill_id": "system.not_a_real_skill", "args": {}}],
            }
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.max_replan_attempts = 1
    daemon.process_pending_once()

    artifacts = queue_root / "artifacts" / "job-goal-inline-unknown"
    execution_result = json.loads((artifacts / "execution_result.json").read_text(encoding="utf-8"))

    assert execution_result["attempt_index"] == 2
    assert execution_result["replan_count"] == 1
    assert execution_result["max_replans"] == 1
    assert execution_result["evaluation_class"] == "replannable_mismatch"
    assert execution_result["evaluation_reason"] == "skill_not_found"
    assert execution_result["stop_reason"] == "terminal_failure"
    assert (artifacts / "plan.attempt-1.json").exists()
    assert (artifacts / "plan.attempt-2.json").exists()


def test_enqueue_child_creates_single_child_with_lineage_and_evidence(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-parent.json").write_text(
        json.dumps(
            {
                "goal": "health",
                "enqueue_child": {"goal": "open diagnostics", "title": "Child diagnostics"},
            }
        )
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    done_parent = queue_root / "done" / "job-parent.json"
    assert done_parent.exists()

    children = sorted((queue_root / "inbox").glob("child-*.json"))
    assert len(children) == 1
    child_payload = json.loads(children[0].read_text(encoding="utf-8"))
    assert child_payload["goal"] == "open diagnostics"
    assert child_payload["parent_job_id"] == "job-parent.json"
    assert child_payload["root_job_id"] == "job-parent.json"
    assert child_payload["orchestration_depth"] == 1
    assert child_payload["sequence_index"] == 1
    assert child_payload["lineage_role"] == "child"

    parent_art = queue_root / "artifacts" / "job-parent"
    child_refs = json.loads((parent_art / "child_job_refs.json").read_text(encoding="utf-8"))
    assert child_refs["child_refs"][0]["child_job_id"] == children[0].name

    actions = (parent_art / "actions.jsonl").read_text(encoding="utf-8")
    assert "queue_child_enqueued" in actions

    result = json.loads((parent_art / "execution_result.json").read_text(encoding="utf-8"))
    assert result["child_refs"][0]["child_job_id"] == children[0].name


def test_enqueue_child_inherits_parent_root_and_depth(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-parent-rooted.json").write_text(
        json.dumps(
            {
                "goal": "health",
                "root_job_id": "root-x.json",
                "orchestration_depth": 4,
                "sequence_index": 9,
                "lineage_role": "root",
                "enqueue_child": {"goal": "child work"},
            }
        )
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    child = sorted((queue_root / "inbox").glob("child-*.json"))[0]
    payload = json.loads(child.read_text(encoding="utf-8"))
    assert payload["root_job_id"] == "root-x.json"
    assert payload["orchestration_depth"] == 5
    assert payload["sequence_index"] == 1
    assert payload["lineage_role"] == "child"


def test_malformed_enqueue_child_fails_closed_without_child_job(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-bad-child.json").write_text(
        json.dumps({"goal": "health", "enqueue_child": {"goal": "  ", "lineage_role": "root"}})
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    assert (queue_root / "failed" / "job-bad-child.json").exists()
    assert sorted((queue_root / "inbox").glob("child-*.json")) == []


def test_enqueue_child_does_not_auto_execute_or_spawn_grandchildren(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)
    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-parent-nested.json").write_text(
        json.dumps(
            {
                "goal": "health",
                "enqueue_child": {"goal": "child", "title": "child"},
            }
        )
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    children = sorted((queue_root / "inbox").glob("child-*.json"))
    assert len(children) == 1
    child_payload = json.loads(children[0].read_text(encoding="utf-8"))
    assert "enqueue_child" not in child_payload


def test_child_job_enters_normal_approval_flow_when_executed(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_plan(monkeypatch)

    async def _plan_open_url(goal, cfg, registry, source="cli", job_ref=None, **_kwargs):
        return MissionTemplate(
            id="plan_open_url",
            title="Open URL",
            goal=goal,
            steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
            notes="stub",
        )

    queue_root = tmp_path / "queue"
    (queue_root / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_root / "inbox" / "job-parent-approval.json").write_text(
        json.dumps({"goal": "health", "enqueue_child": {"goal": "open https://example.com"}})
    )

    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.process_pending_once()

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _plan_open_url)
    daemon.process_pending_once()

    pending_children = sorted(
        p for p in (queue_root / "pending").glob("child-*.json") if daemon._is_primary_job_json(p)
    )
    assert len(pending_children) == 1
    approval = queue_root / "pending" / "approvals" / f"{pending_children[0].stem}.approval.json"
    assert approval.exists()
