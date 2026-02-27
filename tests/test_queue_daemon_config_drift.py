from __future__ import annotations

import json
from pathlib import Path

from voxera.config import load_config
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig


def _force_app_config(monkeypatch) -> None:
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def _runtime_config(path: Path, *, password: str, panel_port: int = 8844) -> None:
    path.write_text(
        json.dumps(
            {
                "queue_root": str(path.parent),
                "panel_operator_password": password,
                "panel_port": panel_port,
            }
        ),
        encoding="utf-8",
    )


def test_daemon_config_drift_emits_once_when_changed(tmp_path: Path, monkeypatch) -> None:
    _force_app_config(monkeypatch)
    config_file = tmp_path / "runtime.json"
    monkeypatch.setenv("VOXERA_RUNTIME_CONFIG", "")

    events: list[dict] = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))

    _runtime_config(config_file, password="first")
    daemon = MissionQueueDaemon(queue_root=tmp_path)
    daemon.settings = load_config(config_path=config_file)
    daemon._snapshot_and_check_config_drift()

    assert (tmp_path / "config_snapshot.json").exists()
    assert (tmp_path / "config_snapshot.last.json").exists()
    assert not [e for e in events if e.get("event") == "config_drift_detected"]

    daemon_same = MissionQueueDaemon(queue_root=tmp_path)
    daemon_same.settings = load_config(config_path=config_file)
    daemon_same._snapshot_and_check_config_drift()
    assert len([e for e in events if e.get("event") == "config_drift_detected"]) == 0

    _runtime_config(config_file, password="first", panel_port=9955)
    daemon_changed = MissionQueueDaemon(queue_root=tmp_path)
    daemon_changed.settings = load_config(config_path=config_file)
    daemon_changed._snapshot_and_check_config_drift()

    drift_events = [e for e in events if e.get("event") == "config_drift_detected"]
    assert len(drift_events) == 1
    drift = drift_events[0]
    assert drift["changed"] is True
    assert drift["old_hash"] != drift["new_hash"]
    assert drift["changed_keys"]
    assert "first" not in json.dumps(drift)
    note = (tmp_path / "config_drift_note.txt").read_text(encoding="utf-8")
    assert "old_hash=" in note
    assert "new_hash=" in note
