from __future__ import annotations

import json

from fastapi.testclient import TestClient

from voxera.panel import app as panel_module


def test_panel_home_renders_queue_and_mission_log(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    mission_log = fake_home / "VoxeraOS" / "notes" / "mission-log.md"
    mission_log.parent.mkdir(parents=True, exist_ok=True)
    mission_log.write_text("\n".join(f"line-{i}" for i in range(30)), encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    assert "Queue Status" in body
    assert "Failed Metadata Health" in body
    assert "Failed metadata valid" in body
    assert "Pending Queue Approvals" in body
    assert "Mission Log (last 20 lines)" in body
    assert "line-29" in body
    assert "line-8" not in body


def test_panel_home_shows_not_found_hints(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    expected_queue = str(fake_home / "VoxeraOS" / "notes" / "queue")
    expected_log = str(fake_home / "VoxeraOS" / "notes" / "mission-log.md")
    assert f"Queue root not found: {expected_queue}" in body
    assert f"Mission log not found: {expected_log}" in body


def test_panel_can_click_approve_pending_queue_job(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-e2e-ask.json").write_text(
        json.dumps({"goal": "demo"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-e2e-ask.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "demo"},
                "resume_step": 1,
                "mission": {
                    "id": "demo",
                    "title": "Demo",
                    "goal": "demo",
                    "steps": [{"skill_id": "system.status", "args": {}}],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-e2e-ask.approval.json").write_text(
        json.dumps(
            {
                "job": "job-e2e-ask.json",
                "step": 1,
                "skill": "system.open_url",
                "reason": "needs approval",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    body = client.get("/").text
    assert "Approve" in body
    assert "job-e2e-ask.json" in body

    res = client.post("/queue/approvals/job-e2e-ask.json/approve", follow_redirects=False)
    assert res.status_code == 303
    assert (queue_dir / "done" / "job-e2e-ask.json").exists()
