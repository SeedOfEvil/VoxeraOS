from __future__ import annotations

import json

from fastapi.testclient import TestClient

from voxera.audit import log
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
    assert "Voxera Control Pane" in body
    assert "Control" in body
    assert "Logging" in body
    assert "Queue Status + Failed Metadata Health" in body
    assert "Failed retention max age (s)" in body
    assert "Latest prune removed jobs/sidecars" in body
    assert "Approval Command Center" in body
    assert "Active Work" in body
    assert "Mission Library" in body
    assert "Approval Command Center" in body
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
    expected_log = str(fake_home / "VoxeraOS" / "notes" / "mission-log.md")
    assert f"Mission log not found: {expected_log}" in body
    assert "Active Work" in body


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


def test_panel_queue_create_goal_and_mission(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)

    goal_res = client.post("/queue/create", data={"kind": "goal", "goal": "run system check"})
    assert goal_res.status_code == 200
    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text(encoding="utf-8"))
    assert payload == {"goal": "run system check"}

    mission_res = client.post(
        "/queue/create", data={"kind": "mission", "mission_id": "system_check"}
    )
    assert mission_res.status_code == 200
    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 2


def test_panel_create_mission_template(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.post(
        "/missions/create",
        data={
            "mission_id": "custom_status",
            "title": "Custom Status",
            "goal": "Get system status",
            "steps_json": '[{"skill_id":"system.status","args":{}}]',
        },
    )
    assert res.status_code == 200

    mission_file = fake_home / ".config" / "voxera" / "missions" / "custom_status.json"
    assert mission_file.exists()


def test_panel_active_work_from_audit(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    log(
        {
            "event": "queue_job_started",
            "job": str(fake_home / "VoxeraOS/notes/queue/job-1.json"),
            "goal": "demo",
        }
    )
    client = TestClient(panel_module.app)
    body = client.get("/").text
    assert "job-1.json" in body
    assert "queue_job_started" in body


def test_panel_get_mutations_disabled_by_default(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.delenv("VOXERA_PANEL_ENABLE_GET_MUTATIONS", raising=False)

    client = TestClient(panel_module.app)

    queue_res = client.get("/queue/create", follow_redirects=False)
    assert queue_res.status_code == 405

    mission_res = client.get("/missions/create", follow_redirects=False)
    assert mission_res.status_code == 405


def test_panel_get_mutations_compat_mode(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_ENABLE_GET_MUTATIONS", "1")

    client = TestClient(panel_module.app)
    queue_res = client.get("/queue/create", params={"kind": "goal", "goal": "legacy goal"})
    assert queue_res.status_code == 200

    mission_res = client.get(
        "/missions/create",
        params={
            "mission_id": "legacy_status",
            "steps_json": '[{"skill_id":"system.status","args":{}}]',
        },
    )
    assert mission_res.status_code == 200

    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 1

    mission_file = fake_home / ".config" / "voxera" / "missions" / "legacy_status.json"
    assert mission_file.exists()


def test_panel_queue_create_validation_errors(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)

    missing_goal = client.post(
        "/queue/create", data={"kind": "goal", "goal": ""}, follow_redirects=False
    )
    assert missing_goal.status_code == 303
    assert missing_goal.headers["location"] == "/?error=goal_required"

    missing_mission = client.post(
        "/queue/create", data={"kind": "mission", "mission_id": ""}, follow_redirects=False
    )
    assert missing_mission.status_code == 303
    assert missing_mission.headers["location"] == "/?error=mission_id_required"

    bad_kind = client.post("/queue/create", data={"kind": "other"}, follow_redirects=False)
    assert bad_kind.status_code == 303
    assert bad_kind.headers["location"] == "/?error=queue_kind_invalid"


def test_panel_create_mission_validation_errors(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    bad_json = client.post(
        "/missions/create",
        data={"mission_id": "custom_status", "steps_json": "{"},
        follow_redirects=False,
    )
    assert bad_json.status_code == 303
    assert bad_json.headers["location"] == "/?error=steps_json_invalid"

    not_list = client.post(
        "/missions/create",
        data={"mission_id": "custom_status", "steps_json": '{"skill_id":"system.status"}'},
        follow_redirects=False,
    )
    assert not_list.status_code == 303
    assert not_list.headers["location"] == "/?error=steps_json_not_list"

    schema_invalid = client.post(
        "/missions/create",
        data={"mission_id": "custom_status", "steps_json": '[{"args":{}}]'},
        follow_redirects=False,
    )
    assert schema_invalid.status_code == 303
    assert schema_invalid.headers["location"] == "/?error=mission_schema_invalid"

    mission_file = fake_home / ".config" / "voxera" / "missions" / "custom_status.json"
    assert not mission_file.exists()


def test_panel_rejects_invalid_mission_id_values(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    for mission_id in ["../x", "a/b", "invalid id", "UPPERCASE"]:
        res = client.post(
            "/missions/create",
            data={
                "mission_id": mission_id,
                "steps_json": '[{"skill_id":"system.status","args":{}}]',
            },
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/?error=mission_id_invalid"


def test_panel_app_uses_shared_version_source():
    assert panel_module.app.version == panel_module.get_version()


def test_panel_job_detail_smoke(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    art = queue_dir / "artifacts" / "job-a"
    art.mkdir(parents=True, exist_ok=True)
    (art / "plan.json").write_text('{"x":1}', encoding="utf-8")
    (art / "actions.jsonl").write_text('{"event":"one","ts":1}\n', encoding="utf-8")
    (art / "stdout.txt").write_text("out", encoding="utf-8")
    (art / "stderr.txt").write_text("err", encoding="utf-8")
    (art / "outputs").mkdir(parents=True, exist_ok=True)
    (art / "outputs" / "generated_files.json").write_text('["a.txt"]', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/queue/jobs/job-a.json/detail")
    assert res.status_code == 200
    assert "stdout" in res.text
    assert "generated" in res.text.lower()
