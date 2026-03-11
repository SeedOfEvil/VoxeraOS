from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.operator_assistant import build_assistant_messages, read_assistant_thread
from voxera.panel.assistant import enqueue_assistant_question, read_assistant_result


def _write_assistant_job(
    queue_root: Path,
    *,
    question: str = "What is happening right now?",
    thread_id: str = "thread-test",
) -> Path:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job = inbox / "job-assistant-test.json"
    job.write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": question,
                "thread_id": thread_id,
                "advisory": True,
                "read_only": True,
            }
        ),
        encoding="utf-8",
    )
    return job


def _stub_answer(daemon: MissionQueueDaemon, *, answer: str = "queue advisory answer") -> None:
    daemon._assistant_answer_via_brain = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "answer": answer,
        "provider": "primary",
        "model": "demo-primary",
        "fallback_used": False,
        "fallback_reason": None,
        "advisory_mode": "queue",
        "degraded_reason": None,
    }


def test_assistant_job_runs_through_queue_read_only(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("mission runner should not execute for assistant jobs")

    daemon.mission_runner.run = _fail_if_called  # type: ignore[method-assign]
    _stub_answer(daemon)
    job = _write_assistant_job(queue_root)

    processed = daemon.process_job_file(job)
    assert processed is True

    done_job = queue_root / "done" / "job-assistant-test.json"
    assert done_job.exists()
    response_artifact = queue_root / "artifacts" / "job-assistant-test" / "assistant_response.json"
    assert response_artifact.exists()
    payload = json.loads(response_artifact.read_text(encoding="utf-8"))
    assert payload["kind"] == "assistant_question"
    assert payload["thread_id"] == "thread-test"
    assert payload["advisory_mode"] == "queue"
    assert payload["fallback_used"] is False


def test_assistant_job_failure_persists_failed_state(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    bad_job = _write_assistant_job(queue_root, question="")

    processed = daemon.process_job_file(bad_job)
    assert processed is False
    failed_job = queue_root / "failed" / "job-assistant-test.json"
    assert failed_job.exists()


def test_assistant_result_reader_surfaces_answer_and_metadata(tmp_path):
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True, exist_ok=True)
    (queue_root / "done" / "job-assistant-test.json").write_text(
        json.dumps({"thread_id": "thread-test"}), encoding="utf-8"
    )
    (queue_root / "artifacts" / "job-assistant-test").mkdir(parents=True, exist_ok=True)
    (queue_root / "artifacts" / "job-assistant-test" / "assistant_response.json").write_text(
        json.dumps(
            {
                "answer": "Control-plane view: queue is clear.",
                "updated_at_ms": 1,
                "provider": "fallback",
                "model": "fast-model",
                "fallback_used": True,
                "fallback_reason": "TIMEOUT",
                "advisory_mode": "queue",
            }
        ),
        encoding="utf-8",
    )

    result = read_assistant_result(queue_root, "job-assistant-test.json")
    assert result["status"] == "answered"
    assert "queue is clear" in result["answer"]
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "TIMEOUT"


def test_assistant_brain_candidates_prefers_fast_for_assistant_default():
    from voxera.core import queue_assistant

    cfg = SimpleNamespace(
        brain={
            "primary": SimpleNamespace(type="openai_compat", model="m-primary"),
            "fast": SimpleNamespace(type="openai_compat", model="m-fast"),
            "fallback": SimpleNamespace(type="openai_compat", model="m-fallback"),
        }
    )

    candidates = queue_assistant.assistant_brain_candidates(cfg)

    assert [name for name, _ in candidates] == ["fast", "primary", "fallback"]


def test_assistant_advisory_primary_success_metadata(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.brain = {
        "primary": SimpleNamespace(
            type="openai_compat", model="m-primary", base_url="", api_key_ref="", extra_headers={}
        ),
        "fallback": SimpleNamespace(
            type="openai_compat", model="m-fallback", base_url="", api_key_ref="", extra_headers={}
        ),
    }

    class _Brain:
        async def generate(self, messages, tools=None):
            return SimpleNamespace(text="primary answered")

    daemon._create_assistant_brain = lambda provider: _Brain()  # type: ignore[method-assign]
    result = daemon._assistant_answer_via_brain("q", {"queue_counts": {}}, thread_turns=[])
    assert result["answer"] == "primary answered"
    assert result["provider"] == "primary"
    assert result["fallback_used"] is False


def test_assistant_advisory_primary_fail_fallback_success(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.brain = {
        "primary": SimpleNamespace(
            type="openai_compat", model="m-primary", base_url="", api_key_ref="", extra_headers={}
        ),
        "fallback": SimpleNamespace(
            type="openai_compat", model="m-fallback", base_url="", api_key_ref="", extra_headers={}
        ),
    }

    class _PrimaryBrain:
        async def generate(self, messages, tools=None):
            raise TimeoutError("timed out")

    class _FallbackBrain:
        async def generate(self, messages, tools=None):
            return SimpleNamespace(text="fallback answered")

    daemon._create_assistant_brain = lambda provider: (
        _PrimaryBrain() if provider.model == "m-primary" else _FallbackBrain()
    )  # type: ignore[method-assign]
    result = daemon._assistant_answer_via_brain("q", {"queue_counts": {}}, thread_turns=[])
    assert result["answer"] == "fallback answered"
    assert result["provider"] == "fallback"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "TIMEOUT"


def test_assistant_advisory_total_failure_moves_to_failed(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.cfg.brain = {
        "primary": SimpleNamespace(
            type="openai_compat", model="m-primary", base_url="", api_key_ref="", extra_headers={}
        ),
        "fallback": SimpleNamespace(
            type="openai_compat", model="m-fallback", base_url="", api_key_ref="", extra_headers={}
        ),
    }

    class _FailBrain:
        async def generate(self, messages, tools=None):
            raise TimeoutError("timed out")

    daemon._create_assistant_brain = lambda provider: _FailBrain()  # type: ignore[method-assign]
    job = _write_assistant_job(queue_root)
    assert daemon.process_job_file(job) is False
    failed_job = queue_root / "failed" / "job-assistant-test.json"
    assert failed_job.exists()
    artifact = json.loads(
        (queue_root / "artifacts" / "job-assistant-test" / "assistant_response.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["error"]
    assert artifact["advisory_mode"] == "queue"


def test_fallback_partner_voice_uses_varied_opening():
    from voxera.operator_assistant import fallback_operator_answer

    context = {
        "queue_counts": {"inbox": 0, "pending": 0, "pending_approvals": 0, "failed": 0, "done": 0},
        "health_current_state": {"daemon_state": "healthy"},
        "queue_paused": False,
        "pending_approvals": [],
        "recent_failed_jobs": [],
        "recent_events": [],
    }
    answer = fallback_operator_answer("What is happening right now?", context)
    assert not answer.startswith("From inside Voxera")
    assert "queue counts" in answer


def test_assistant_thread_persists_multiturn_history(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    _stub_answer(daemon, answer="thread answer")

    first_job, thread_id = enqueue_assistant_question(queue_root, "What is happening right now?")
    assert thread_id
    first_path = queue_root / "inbox" / first_job
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["job_intent"]["source_lane"] == "assistant_advisory"
    assert payload["job_intent"]["request_kind"] == "assistant_question"
    assert daemon.process_job_file(first_path)

    second_job, same_thread = enqueue_assistant_question(
        queue_root,
        "What about approvals?",
        thread_id=thread_id,
    )
    assert same_thread == thread_id
    second_path = queue_root / "inbox" / second_job
    assert daemon.process_job_file(second_path)

    thread_payload = read_assistant_thread(queue_root, thread_id)
    turns = thread_payload["turns"]
    assert len(turns) >= 4
    assert turns[-1]["role"] == "assistant"
    assert any(turn["text"] == "What about approvals?" for turn in turns if turn["role"] == "user")


def test_build_assistant_messages_includes_bounded_history():
    messages = build_assistant_messages(
        "go on",
        {"queue_counts": {"pending": 1}},
        thread_turns=[
            {"role": "user", "text": "What is happening?"},
            {"role": "assistant", "text": "I see pending=1."},
        ],
    )
    assert messages[0]["role"] == "system"
    assert any(msg["content"] == "What is happening?" for msg in messages if msg["role"] == "user")
    assert "Latest operator question" in messages[-1]["content"]
