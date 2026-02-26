from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from voxera.doctor import print_report, run_doctor
from voxera.models import AppConfig, BrainConfig


class _FakeBrain:
    def __init__(self, payload: dict):
        self._payload = payload

    async def capability_test(self):
        return dict(self._payload)


def test_run_doctor_writes_empty_report_when_no_brains(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"

    monkeypatch.setattr("voxera.doctor.load_config", lambda: AppConfig())
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)

    results = asyncio.run(run_doctor())

    assert "sandbox.podman" in results
    assert results["sandbox.podman"]["provider"] == "podman"
    assert (
        json.loads(report_path.read_text(encoding="utf-8"))["sandbox.podman"]["provider"]
        == "podman"
    )


def test_print_report_warns_when_no_brains_configured(capsys):
    print_report({})
    captured = capsys.readouterr()
    assert "No brain providers configured" in captured.out


def test_run_doctor_adds_fallback_note_and_audit_event(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"
    events = []
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", base_url="https://example.com")
        }
    )

    monkeypatch.setattr("voxera.doctor.load_config", lambda: cfg)
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)
    monkeypatch.setattr(
        "voxera.doctor.OpenAICompatBrain", lambda **_: _FakeBrain({"json_ok": False})
    )
    monkeypatch.setattr("voxera.doctor.audit.log", lambda event: events.append(event))

    results = asyncio.run(run_doctor())

    assert (
        results["primary"]["note"]
        == "invalid_json: capability_test returned json_ok=false (no details)"
    )
    assert events[0]["event"] == "doctor_brain_test"
    assert events[0]["brain"] == "primary"
    assert events[0]["json_ok"] is False
    assert events[0]["note"] == results["primary"]["note"]


def test_run_doctor_ignores_audit_oserror(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="m1", base_url="https://example.com")
        }
    )

    monkeypatch.setattr("voxera.doctor.load_config", lambda: cfg)
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)
    monkeypatch.setattr(
        "voxera.doctor.OpenAICompatBrain", lambda **_: _FakeBrain({"json_ok": True})
    )

    def _raise_oserror(event):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("voxera.doctor.audit.log", _raise_oserror)

    results = asyncio.run(run_doctor())

    assert results["primary"]["json_ok"] is True
    assert report_path.exists()


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def test_openai_compat_capability_test_malformed_json(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        return _FakeResponse("<not-json>")

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"].startswith("malformed_json:")


def test_openai_compat_capability_test_strips_markdown_fence(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        return _FakeResponse(
            """```json
{"title":"T","goal":"G","steps":[{"skill_id":"system.status","args":{}}]}
```"""
        )

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is True
    assert result["note"] == "stripped_markdown_fence"


def test_openai_compat_capability_test_extracts_json_object(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        return _FakeResponse(
            'Here you go: {"title":"T","goal":"G","steps":[{"skill_id":"system.status","args":{}}]} Thanks'
        )

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is True
    assert result["note"] == "extracted_json_object"


def test_openai_compat_capability_test_http_error(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        req = httpx.Request("POST", "https://api.example.com/chat/completions")
        resp = httpx.Response(status_code=500, request=req, text="server error")
        raise httpx.HTTPStatusError("boom", request=req, response=resp)

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "http_error:500"


def test_openai_compat_capability_test_rate_limit(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        req = httpx.Request("POST", "https://api.example.com/chat/completions")
        resp = httpx.Response(status_code=429, request=req, text="rate limited")
        raise httpx.HTTPStatusError("boom", request=req, response=resp)

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "rate_limit"


def test_openai_compat_capability_test_timeout(monkeypatch):
    from voxera.brain.openai_compat import OpenAICompatBrain

    async def _fake_generate(self, messages, tools=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(OpenAICompatBrain, "generate", _fake_generate)
    brain = OpenAICompatBrain(base_url="https://api.example.com", model="x")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "timeout"


def test_gemini_capability_test_strips_markdown_fence(monkeypatch):
    from voxera.brain.gemini import GeminiBrain

    async def _fake_generate(self, messages, tools=None):
        return _FakeResponse(
            """```
{"title":"T","goal":"G","steps":[{"skill_id":"system.status","args":{}}]}
```"""
        )

    monkeypatch.setattr(GeminiBrain, "generate", _fake_generate)
    brain = GeminiBrain(model="gemini-2.0-flash")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is True
    assert result["note"] == "stripped_markdown_fence"


def test_gemini_capability_test_extracts_json_object(monkeypatch):
    from voxera.brain.gemini import GeminiBrain

    async def _fake_generate(self, messages, tools=None):
        return _FakeResponse(
            'Plan: {"title":"T","goal":"G","steps":[{"skill_id":"system.status","args":{}}]} done'
        )

    monkeypatch.setattr(GeminiBrain, "generate", _fake_generate)
    brain = GeminiBrain(model="gemini-2.0-flash")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is True
    assert result["note"] == "extracted_json_object"


def test_gemini_capability_test_http_error(monkeypatch):
    from voxera.brain.gemini import GeminiBrain

    async def _fake_generate(self, messages, tools=None):
        raise RuntimeError("Gemini provider error HTTP 401: unauthorized")

    monkeypatch.setattr(GeminiBrain, "generate", _fake_generate)
    brain = GeminiBrain(model="gemini-2.0-flash")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "http_error:401"


def test_gemini_capability_test_rate_limit(monkeypatch):
    from voxera.brain.gemini import GeminiBrain

    async def _fake_generate(self, messages, tools=None):
        raise RuntimeError("Gemini rate limit (429)")

    monkeypatch.setattr(GeminiBrain, "generate", _fake_generate)
    brain = GeminiBrain(model="gemini-2.0-flash")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "rate_limit"


def test_gemini_capability_test_timeout(monkeypatch):
    from voxera.brain.gemini import GeminiBrain

    async def _fake_generate(self, messages, tools=None):
        raise RuntimeError("Planner timed out contacting Gemini")

    monkeypatch.setattr(GeminiBrain, "generate", _fake_generate)
    brain = GeminiBrain(model="gemini-2.0-flash")

    result = asyncio.run(brain.capability_test())

    assert result["json_ok"] is False
    assert result["note"] == "timeout"


def test_run_self_test_returns_fix_steps_when_artifacts_missing(monkeypatch):
    from voxera.doctor import run_self_test

    class _FakeDaemon:
        def __init__(self, queue_root):
            self.queue_root = queue_root

        def ensure_dirs(self):
            (self.queue_root / "done").mkdir(parents=True, exist_ok=True)

        def process_pending_once(self):
            job = self.queue_root / "inbox" / "doctor-self-test.json"
            (self.queue_root / "done" / "doctor-self-test.json").write_text(
                job.read_text(encoding="utf-8"), encoding="utf-8"
            )
            return 1

    monkeypatch.setattr("voxera.doctor.MissionQueueDaemon", _FakeDaemon)
    monkeypatch.setattr("voxera.doctor.audit.tail", lambda _n: [{"event": "queue_job_done"}])

    result = run_self_test(timeout_s=1.0)

    assert result["ok"] is False
    assert result["missing_artifacts"]
    assert result["fixes"]


def test_doctor_quick_offline_does_not_call_brains(monkeypatch, tmp_path):
    from voxera.doctor import run_quick_doctor

    queue_root = tmp_path / "queue"
    for path in [
        queue_root / "inbox",
        queue_root / "pending",
        queue_root / "pending" / "approvals",
        queue_root / "done",
        queue_root / "failed",
        queue_root / "artifacts",
        queue_root / "_archive",
    ]:
        path.mkdir(parents=True, exist_ok=True)

    called = {"openai": False, "gemini": False}

    async def _never(*args, **kwargs):
        called["openai"] = True
        raise AssertionError("should not call OpenAI in quick doctor")

    monkeypatch.setattr("voxera.brain.openai_compat.OpenAICompatBrain.capability_test", _never)
    monkeypatch.setattr("voxera.brain.gemini.GeminiBrain.capability_test", _never)

    checks = run_quick_doctor(queue_root=queue_root)

    assert checks
    assert called["openai"] is False
    assert called["gemini"] is False
