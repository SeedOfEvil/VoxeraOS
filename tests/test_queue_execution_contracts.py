from __future__ import annotations

import json

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
    assert envelope["execution"]["steps"][0]["skill_id"] == "system.status"


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
        json.dumps({"kind": "assistant_question", "question": "What changed?", "thread_id": "t-1"})
    )

    daemon.process_pending_once()

    execution_result = json.loads(
        (queue_root / "artifacts" / "job-assistant" / "execution_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_result["ok"] is True
    assert execution_result["step_results"][0]["skill_id"] == "assistant.advisory"
