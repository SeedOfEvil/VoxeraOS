from __future__ import annotations

import asyncio
import json
from pathlib import Path

from voxera.doctor import print_report, run_doctor
from voxera.models import AppConfig, BrainConfig


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


def test_doctor_reports_malformed_json_note(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"
    cfg = AppConfig(
        brain={
            "fast": BrainConfig(type="openai_compat", model="test-model", base_url="https://example.com")
        }
    )

    class FakeBrain:
        def __init__(self, **kwargs):
            del kwargs

        async def capability_test(self):
            return {
                "provider": "openai_compat",
                "model": "test-model",
                "json_ok": False,
                "latency_s": 0.123,
                "note": "malformed_json: not-json-output",
            }

    monkeypatch.setattr("voxera.doctor.load_config", lambda: cfg)
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)
    monkeypatch.setattr("voxera.doctor.OpenAICompatBrain", FakeBrain)

    results = asyncio.run(run_doctor())

    assert results["fast"]["json_ok"] is False
    note = (results["fast"].get("note") or results["fast"].get("error") or "").lower()
    assert "malformed_json" in note


def test_doctor_reports_provider_status_errors(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"
    cfg = AppConfig(
        brain={
            "fast": BrainConfig(type="openai_compat", model="test-model", base_url="https://example.com")
        }
    )

    class FakeBrain:
        def __init__(self, **kwargs):
            del kwargs

        async def capability_test(self):
            return {
                "provider": "openai_compat",
                "model": "test-model",
                "json_ok": False,
                "latency_s": 0.456,
                "note": "HTTPStatusError: status=401",
            }

    monkeypatch.setattr("voxera.doctor.load_config", lambda: cfg)
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)
    monkeypatch.setattr("voxera.doctor.OpenAICompatBrain", FakeBrain)

    results = asyncio.run(run_doctor())

    assert results["fast"]["json_ok"] is False
    note = (results["fast"].get("note") or results["fast"].get("error") or "").lower()
    assert "401" in note or "httpstatuserror" in note or "429" in note


def test_print_report_warns_when_no_brains_configured(capsys):
    print_report({})
    captured = capsys.readouterr()
    assert "No brain providers configured" in captured.out
