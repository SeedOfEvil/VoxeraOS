from __future__ import annotations

import asyncio
import json
from pathlib import Path

from voxera.doctor import print_report, run_doctor
from voxera.models import AppConfig


def test_run_doctor_writes_empty_report_when_no_brains(monkeypatch, tmp_path: Path):
    report_path = tmp_path / "capabilities.json"

    monkeypatch.setattr("voxera.doctor.load_config", lambda: AppConfig())
    monkeypatch.setattr("voxera.doctor.capabilities_report_path", lambda: report_path)

    results = asyncio.run(run_doctor())

    assert "sandbox.podman" in results
    assert results["sandbox.podman"]["provider"] == "podman"
    assert json.loads(report_path.read_text(encoding="utf-8"))["sandbox.podman"]["provider"] == "podman"


def test_print_report_warns_when_no_brains_configured(capsys):
    print_report({})
    captured = capsys.readouterr()
    assert "No brain providers configured" in captured.out
