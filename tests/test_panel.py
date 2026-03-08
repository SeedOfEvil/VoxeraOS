from __future__ import annotations

import base64
import io
import json
import zipfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from voxera.audit import log
from voxera.panel import app as panel_module


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(client: TestClient, method: str, url: str, *, data: dict[str, str]):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data)
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


def test_panel_home_renders_queue_and_mission_log(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    mission_log = fake_home / "VoxeraOS" / "notes" / "mission-log.md"
    mission_log.parent.mkdir(parents=True, exist_ok=True)
    mission_log.write_text("\n".join(f"line-{i}" for i in range(30)), encoding="utf-8")
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "last_ok_event": "daemon_tick",
                "last_ok_ts_ms": 123,
                "last_error": "none",
                "last_error_ts_ms": 122,
            }
        ),
        encoding="utf-8",
    )
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
    assert "Daemon Lock Event Counters" in body
    assert "Last OK" in body
    assert "daemon_tick @ 123" in body
    assert "Last Error" in body
    assert "Panel Mutation Security Counters" in body
    assert "Create Mission" in body
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


def test_home_renders_daemon_health_widget_with_empty_health(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    assert "Daemon Health" in body
    assert "notes/queue/health.json" in body
    assert "No recent fallbacks." in body
    assert "clean" in body
    assert "unknown" in body
    assert "Status" in body
    assert "clear" in body
    assert "healthy" in body


def test_home_renders_daemon_health_widget_with_fields(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "lock_status": {"status": "stale", "pid": 4321, "stale_age_s": 125},
                "last_fallback_to": "fallback",
                "last_fallback_reason": "RATE_LIMIT",
                "last_fallback_ts_ms": 1700000000000,
                "last_startup_recovery_counts": {
                    "jobs_failed": 2,
                    "orphan_approvals_quarantined": 1,
                    "orphan_state_files_quarantined": 3,
                },
                "last_startup_recovery_ts": 1700000001000,
                "last_shutdown_outcome": "failed_shutdown",
                "last_shutdown_ts": 1700000002.0,
                "last_shutdown_reason": "KeyboardInterrupt",
                "last_shutdown_job": "job-5.json",
                "daemon_state": "degraded",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    assert "Daemon Health" in body
    assert "stale" in body
    assert "4321" in body
    assert "2m 5s" in body
    assert "fallback" in body
    assert "RATE_LIMIT" in body
    assert "2023-11-14 22:13:20 UTC" in body
    assert "job_count" in body
    assert ">2<" in body
    assert "orphan_count" in body
    assert ">4<" in body
    assert "failed_shutdown" in body
    assert "KeyboardInterrupt" in body
    assert "job-5.json" in body
    assert "degraded" in body


def test_home_renders_performance_stats_tab(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "daemon_state": "degraded",
                "consecutive_brain_failures": 5,
                "brain_backoff_active": True,
                "brain_backoff_wait_s": 8,
                "last_fallback_reason": "timeout",
                "last_fallback_from": "primary",
                "last_fallback_to": "fallback",
                "last_error": "boom",
                "counters": {
                    "panel_auth_invalid": 3,
                    "brain_fallback_count": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    assert "Performance Stats" in body
    assert "Queue Counts" in body
    assert "Current State" in body
    assert "Historical Counters" in body
    assert "consecutive failures" in body
    assert "timeout" in body


def test_home_performance_history_missing_shows_dash(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/")

    assert res.status_code == 200
    body = res.text
    assert "<td>last error</td><td>-</td>" in body
    assert "<td>last fallback</td><td>-</td>" in body
    assert "<td>last shutdown</td><td>-</td>" in body


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
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    body = client.get("/", headers=_operator_headers()).text
    assert "Approve" in body
    assert "job-e2e-ask.json" in body

    res = _authed_csrf_request(client, "post", "/queue/approvals/job-e2e-ask.json/approve", data={})
    assert res.status_code == 303
    assert (queue_dir / "done" / "job-e2e-ask.json").exists()


def test_panel_queue_create_goal_and_mission(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)

    goal_res = _authed_csrf_request(
        client, "post", "/queue/create", data={"kind": "goal", "goal": "run system check"}
    )
    assert goal_res.status_code == 303
    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "run system check"
    assert payload["job_intent"]["request_kind"] == "goal"
    assert payload["job_intent"]["source_lane"] == "panel_queue_create"

    mission_res = _authed_csrf_request(
        client, "post", "/queue/create", data={"kind": "mission", "mission_id": "system_check"}
    )
    assert mission_res.status_code == 303
    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 2


def test_panel_create_mission_template(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    res = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={
            "mission_id": "custom_status",
            "title": "Custom Status",
            "goal": "Get system status",
            "steps_json": '[{"skill_id":"system.status","args":{}}]',
        },
    )
    assert res.status_code == 303

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

    mission_res = client.get("/missions/templates/create", follow_redirects=False)
    assert mission_res.status_code == 405


def test_panel_get_mutations_compat_mode(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_ENABLE_GET_MUTATIONS", "1")
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    queue_res = client.get(
        "/queue/create",
        params={"kind": "goal", "goal": "legacy goal"},
        headers=_operator_headers(),
    )
    assert queue_res.status_code == 200

    mission_res = client.get(
        "/missions/templates/create",
        params={
            "mission_id": "legacy_status",
            "steps_json": '[{"skill_id":"system.status","args":{}}]',
        },
        headers=_operator_headers(),
    )
    assert mission_res.status_code == 200

    queued = list((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert len(queued) == 1

    mission_file = fake_home / ".config" / "voxera" / "missions" / "legacy_status.json"
    assert mission_file.exists()


def test_panel_queue_create_validation_errors(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)

    missing_goal = _authed_csrf_request(
        client, "post", "/queue/create", data={"kind": "goal", "goal": ""}
    )
    assert missing_goal.status_code == 303
    assert missing_goal.headers["location"] == "/?error=goal_required"

    missing_mission = _authed_csrf_request(
        client, "post", "/queue/create", data={"kind": "mission", "mission_id": ""}
    )
    assert missing_mission.status_code == 303
    assert missing_mission.headers["location"] == "/?error=mission_id_required"

    bad_kind = _authed_csrf_request(client, "post", "/queue/create", data={"kind": "other"})
    assert bad_kind.status_code == 303
    assert bad_kind.headers["location"] == "/?error=queue_kind_invalid"


def test_panel_create_mission_validation_errors(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    bad_json = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "custom_status", "steps_json": "{"},
    )
    assert bad_json.status_code == 303
    assert bad_json.headers["location"] == "/?error=steps_json_invalid"

    not_list = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "custom_status", "steps_json": '{"skill_id":"system.status"}'},
    )
    assert not_list.status_code == 303
    assert not_list.headers["location"] == "/?error=steps_json_not_list"

    schema_invalid = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "custom_status", "steps_json": '[{"args":{}}]'},
    )
    assert schema_invalid.status_code == 303
    assert schema_invalid.headers["location"] == "/?error=mission_schema_invalid"

    mission_file = fake_home / ".config" / "voxera" / "missions" / "custom_status.json"
    assert not mission_file.exists()


def test_panel_rejects_invalid_mission_id_values(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    for mission_id in ["../x", "a/b", "invalid id", "UPPERCASE"]:
        res = _authed_csrf_request(
            client,
            "post",
            "/missions/templates/create",
            data={
                "mission_id": mission_id,
                "steps_json": '[{"skill_id":"system.status","args":{}}]',
            },
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/?error=mission_id_invalid"


def test_panel_mission_id_validation_boundaries_and_message(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    valid_steps = '[{"skill_id":"system.status","args":{}}]'

    one_char = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "a", "steps_json": valid_steps},
    )
    assert one_char.status_code == 303
    assert one_char.headers["location"] == "/?error=mission_id_invalid"

    two_chars_alnum = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "a0", "steps_json": valid_steps},
    )
    assert two_chars_alnum.status_code == 303
    assert two_chars_alnum.headers["location"].startswith("/?mission_created=")

    two_chars_with_underscore = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": "a_", "steps_json": valid_steps},
    )
    assert two_chars_with_underscore.status_code == 303
    assert two_chars_with_underscore.headers["location"].startswith("/?mission_created=")

    sixty_four_chars = "a" + ("0" * 63)
    max_len = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": sixty_four_chars, "steps_json": valid_steps},
    )
    assert max_len.status_code == 303
    assert max_len.headers["location"].startswith("/?mission_created=")

    sixty_five_chars = "a" + ("0" * 64)
    too_long = _authed_csrf_request(
        client,
        "post",
        "/missions/templates/create",
        data={"mission_id": sixty_five_chars, "steps_json": valid_steps},
    )
    assert too_long.status_code == 303
    assert too_long.headers["location"] == "/?error=mission_id_invalid"

    for mission_id in ["A0", "a b", "a$"]:
        invalid = _authed_csrf_request(
            client,
            "post",
            "/missions/templates/create",
            data={"mission_id": mission_id, "steps_json": valid_steps},
        )
        assert invalid.status_code == 303
        assert invalid.headers["location"] == "/?error=mission_id_invalid"

    home = client.get("/?error=mission_id_invalid", headers=_operator_headers())
    assert home.status_code == 200
    assert (
        panel_module.ERROR_MESSAGES["mission_id_invalid"]
        == "Mission ID must be 2-64 characters and use lowercase letters, numbers, '_' or '-'."
    )
    assert "Mission ID must be 2-64 characters" in home.text


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


def test_panel_create_mission_requires_auth_and_csrf(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    unauth = client.post("/missions/create", data={"prompt": "run status"})
    assert unauth.status_code == 401

    auth_only = client.post(
        "/missions/create",
        data={"prompt": "run status", "approval_required": "1"},
        headers=_operator_headers(),
        follow_redirects=False,
    )
    assert auth_only.status_code == 403


def test_panel_create_mission_prompt_only_writes_inbox_job(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    ok = _authed_csrf_request(
        client,
        "post",
        "/missions/create",
        data={"prompt": "Open status and summarize health"},
    )
    assert ok.status_code == 303
    assert "created=job-panel-mission-" in ok.headers["location"]

    inbox = fake_home / "VoxeraOS" / "notes" / "queue" / "inbox"
    jobs = sorted(inbox.glob("job-panel-mission-*.json"))
    assert len(jobs) == 1

    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload.get("goal") == "Open status and summarize health"
    assert isinstance(payload.get("id"), str)
    assert payload["id"]
    assert panel_module.MISSION_ID_RE.fullmatch(payload["id"])
    assert payload.get("approval_required") is True

    for key in ("type", "template", "skill", "mission", "dispatch"):
        assert key not in payload


def test_panel_create_mission_goal_alias_writes_inbox_job(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    ok = _authed_csrf_request(
        client,
        "post",
        "/missions/create",
        data={"goal": "Goal alias path should work"},
    )
    assert ok.status_code == 303
    assert "created=job-panel-mission-" in ok.headers["location"]

    inbox = fake_home / "VoxeraOS" / "notes" / "queue" / "inbox"
    jobs = sorted(inbox.glob("job-panel-mission-*.json"))
    assert len(jobs) == 1

    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload.get("goal") == "Goal alias path should work"
    assert isinstance(payload.get("id"), str)
    assert payload["id"]
    assert panel_module.MISSION_ID_RE.fullmatch(payload["id"])


def test_panel_create_mission_validation_error_redirects(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    res = _authed_csrf_request(client, "post", "/missions/create", data={"prompt": ""})
    assert res.status_code == 303
    assert res.headers["location"] == "/?error=panel_prompt_required"


def test_panel_post_requires_auth_and_csrf(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    unauth = client.post("/queue/create", data={"kind": "goal", "goal": "x"})
    assert unauth.status_code == 401

    auth_only = client.post(
        "/queue/create", data={"kind": "goal", "goal": "x"}, headers=_operator_headers()
    )
    assert auth_only.status_code == 403


def test_panel_auth_csrf_failures_emit_counters_and_logs(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    events: list[dict[str, object]] = []
    monkeypatch.setattr(panel_module, "log", lambda e: events.append(e))

    client = TestClient(panel_module.app)

    unauth = client.post("/queue/create", data={"kind": "goal", "goal": "x"})
    assert unauth.status_code == 401

    bad_creds = client.post(
        "/queue/create",
        data={"kind": "goal", "goal": "x"},
        headers=_operator_headers(password="wrong"),
    )
    assert bad_creds.status_code == 401

    auth_only = client.post(
        "/queue/create", data={"kind": "goal", "goal": "x"}, headers=_operator_headers()
    )
    assert auth_only.status_code == 403

    ok = _authed_csrf_request(
        client,
        "post",
        "/queue/create",
        data={"kind": "goal", "goal": "works"},
    )
    assert ok.status_code == 303

    health_path = fake_home / "VoxeraOS" / "notes" / "queue" / "health.json"
    payload = json.loads(health_path.read_text(encoding="utf-8"))
    counters = payload.get("counters", {})
    assert counters.get("panel_401_count", 0) >= 2
    assert counters.get("panel_403_count", 0) >= 1
    assert counters.get("panel_auth_invalid", 0) >= 1
    assert counters.get("panel_csrf_missing", 0) >= 1
    assert counters.get("panel_mutation_allowed", 0) >= 1

    event_names = {str(e.get("event", "")) for e in events}
    assert "panel_auth_missing" in event_names
    assert "panel_auth_invalid" in event_names
    assert "panel_csrf_missing" in event_names
    assert "panel_mutation_allowed" in event_names


def test_jobs_page_filters_by_bucket(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-pending-1.json").write_text(
        json.dumps({"goal": "alpha goal"}), encoding="utf-8"
    )
    (queue_dir / "done" / "job-done-1.json").write_text(
        json.dumps({"goal": "beta goal"}), encoding="utf-8"
    )
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    pending = client.get("/jobs", params={"bucket": "pending", "q": "alpha"})
    assert pending.status_code == 200
    assert "job-pending-1.json" in pending.text
    assert "job-done-1.json" not in pending.text


def test_jobs_page_shows_bucket_artifacts_and_actions(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for d in ["inbox", "pending", "pending/approvals", "done", "failed"]:
        (queue_dir / d).mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "job-inbox.json").write_text('{"goal":"in"}', encoding="utf-8")
    (queue_dir / "pending" / "job-pending.json").write_text('{"goal":"p"}', encoding="utf-8")
    (queue_dir / "done" / "job-done.json").write_text('{"goal":"d"}', encoding="utf-8")
    (queue_dir / "failed" / "job-failed.json").write_text('{"goal":"f"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-pending"
    art.mkdir(parents=True, exist_ok=True)
    (art / "actions.jsonl").write_text('{"event":"step"}\n', encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/jobs", params={"bucket": "all", "n": 20})
    assert res.status_code == 200
    assert "job-inbox.json" in res.text
    assert "job-done.json" in res.text
    assert "pending/approvals" not in res.text
    assert "actions=Y" in res.text
    assert "Bundle" in res.text


def test_jobs_page_excludes_state_sidecars_from_rows(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-real.json").write_text('{"goal":"ok"}', encoding="utf-8")
    (queue_dir / "done" / "job-real.state.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs", params={"bucket": "all", "n": 20})
    assert res.status_code == 200
    assert "job-real.json" in res.text
    assert "job-real.state.json" not in res.text


def test_job_detail_renders_pending_done_and_failed_cases(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)

    (queue_dir / "pending" / "job-pending.json").write_text(
        '{"goal":"needs approval"}', encoding="utf-8"
    )
    (queue_dir / "pending" / "approvals" / "job-pending.approval.json").write_text(
        json.dumps(
            {
                "job": "job-pending.json",
                "policy_reason": "network_changes -> ask",
                "target": {"type": "url", "value": "https://example.com"},
                "scope": {"fs_scope": "workspace_only", "needs_network": True},
            }
        ),
        encoding="utf-8",
    )

    (queue_dir / "done" / "job-done.json").write_text('{"goal":"done"}', encoding="utf-8")
    done_art = queue_dir / "artifacts" / "job-done"
    done_art.mkdir(parents=True, exist_ok=True)
    (done_art / "stdout.txt").write_text("ok", encoding="utf-8")

    (queue_dir / "failed" / "job-failed.json").write_text('{"goal":"failed"}', encoding="utf-8")
    (queue_dir / "failed" / "job-failed.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "job-failed.json",
                "error": "boom",
                "timestamp_ms": 1700000000000,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    pending = client.get("/jobs/job-pending.json")
    assert pending.status_code == 200
    assert "Approval Details" in pending.text

    done = client.get("/jobs/job-done.json")
    assert done.status_code == 200
    assert "Artifact Files" in done.text

    failed = client.get("/jobs/job-failed.json")
    assert failed.status_code == 200
    assert "Failed Sidecar" in failed.text


def test_job_detail_renders_execution_state_fields(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-state.json").write_text('{"goal":"state"}', encoding="utf-8")
    (queue_dir / "done" / "job-state.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": 2,
                "total_steps": 2,
                "approval_status": "approved",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    response = client.get("/jobs/job-state.json")
    assert response.status_code == 200
    assert "Execution State" in response.text
    assert "succeeded" in response.text


def test_job_detail_artifact_inventory_missing_expected_is_soft_anomaly(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "job-failed-anomaly.json").write_text(
        '{"goal":"failed"}', encoding="utf-8"
    )
    # Expected failed sidecar intentionally missing

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-failed-anomaly.json")
    assert res.status_code == 200
    assert "Artifact Inventory" in res.text
    assert "Soft anomaly: Expected artifact missing: failed sidecar" in res.text


def test_job_detail_artifact_inventory_optional_missing_does_not_error(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-done-optional.json").write_text('{"goal":"ok"}', encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-done-optional.json")
    assert res.status_code == 200
    assert "Artifact Inventory" in res.text
    assert "assistant_response.json" in res.text
    assert "missing" in res.text


def test_job_detail_renders_assistant_job_context(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-assistant.json").write_text(
        json.dumps({"goal": "Summarize queue", "title": "Panel assistant"}), encoding="utf-8"
    )
    art = queue_dir / "artifacts" / "job-assistant"
    art.mkdir(parents=True, exist_ok=True)
    (art / "assistant_response.json").write_text(json.dumps({"answer": "ok"}), encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-assistant.json")
    assert res.status_code == 200
    assert "Lifecycle & Context" in res.text
    assert "Recent Action Timeline" in res.text
    assert "assistant_response.json" in res.text


def test_job_detail_prefers_structured_execution_artifacts(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-structured.json").write_text('{"goal":"ok"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-structured"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "succeeded",
                "lifecycle_state": "done",
                "approval_status": "approved",
                "current_step_index": 2,
                "last_attempted_step": 2,
                "last_completed_step": 2,
                "total_steps": 2,
                "step_results": [
                    {
                        "step_index": 2,
                        "skill_id": "assistant.advisory",
                        "status": "succeeded",
                        "summary": "Summarized queue health",
                        "operator_note": "All clear",
                        "next_action_hint": "none",
                        "output_artifacts": ["outputs/summary.md"],
                        "machine_payload": {"risk": "low"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-structured.json")
    assert res.status_code == 200
    assert "Canonical Step Summaries" in res.text
    assert "Structured Execution Hints" in res.text
    assert "Summarized queue health" in res.text
    assert "All clear" in res.text
    assert "outputs/summary.md" in res.text
    assert "Machine payload" in res.text


def test_job_progress_endpoint_surfaces_live_execution_fields(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-live.json").write_text('{"goal":"live"}', encoding="utf-8")
    (queue_dir / "pending" / "job-live.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "awaiting_approval",
                "current_step_index": 1,
                "total_steps": 3,
                "last_attempted_step": 1,
                "last_completed_step": 0,
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-live.approval.json").write_text(
        json.dumps({"job": "job-live.json", "step": 1, "reason": "ask"}), encoding="utf-8"
    )
    art = queue_dir / "artifacts" / "job-live"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "awaiting_approval",
                "execution_lane": "queue",
                "fast_lane": {"eligible": False, "reason": "approval_required"},
                "intent_route": {"intent_kind": "open_url"},
                "stop_reason": "approval_required",
                "current_step_index": 1,
                "total_steps": 3,
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "system.open_url",
                        "status": "awaiting_approval",
                        "summary": "Waiting for approval",
                        "operator_note": "Approval gate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-live.json/progress")
    assert res.status_code == 200
    payload = res.json()
    assert payload["lifecycle_state"] == "awaiting_approval"
    assert payload["approval_status"] == "pending"
    assert payload["execution_lane"] == "queue"
    assert payload["fast_lane"]["reason"] == "approval_required"
    assert payload["intent_route"]["intent_kind"] == "open_url"
    assert payload["latest_summary"] == "Waiting for approval"


def test_job_progress_endpoint_surfaces_terminal_failed_state(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "job-failed.json").write_text('{"goal":"boom"}', encoding="utf-8")
    (queue_dir / "failed" / "job-failed.error.json").write_text(
        json.dumps({"error": "planner rejected route"}), encoding="utf-8"
    )
    art = queue_dir / "artifacts" / "job-failed"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "step_failed",
                "terminal_outcome": "failed",
                "stop_reason": "planner_intent_route_rejected",
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "planner",
                        "status": "failed",
                        "summary": "Rejected",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)

    res = client.get("/jobs/job-failed.json/progress")
    assert res.status_code == 200
    payload = res.json()
    assert payload["bucket"] == "failed"
    assert payload["terminal_outcome"] == "failed"
    assert payload["stop_reason"] == "planner_intent_route_rejected"
    assert payload["failure_summary"]


def test_assistant_progress_endpoint_surfaces_running_and_done(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-assistant-live.json").write_text(
        json.dumps({"kind": "assistant_question", "thread_id": "thread-live"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-assistant-live.state.json").write_text(
        json.dumps({"lifecycle_state": "advisory_running"}), encoding="utf-8"
    )
    art = queue_dir / "artifacts" / "job-assistant-live"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "advisory_running",
                "execution_lane": "fast_read_only",
                "fast_lane": {"eligible": True, "reason": "eligible_read_only_assistant_advisory"},
                "current_step_index": 1,
                "total_steps": 1,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    running = client.get("/assistant/progress/job-assistant-live.json", headers=_operator_headers())
    assert running.status_code == 200
    running_payload = running.json()
    assert running_payload["lifecycle_state"] == "advisory_running"
    assert running_payload["execution_lane"] == "fast_read_only"

    (queue_dir / "pending" / "job-assistant-live.json").unlink()
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-assistant-live.json").write_text(
        json.dumps({"kind": "assistant_question", "thread_id": "thread-live"}), encoding="utf-8"
    )
    (art / "assistant_response.json").write_text(
        json.dumps({"answer": "done", "advisory_mode": "queue"}), encoding="utf-8"
    )
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": 1,
                "total_steps": 1,
            }
        ),
        encoding="utf-8",
    )

    done = client.get("/assistant/progress/job-assistant-live.json", headers=_operator_headers())
    assert done.status_code == 200
    done_payload = done.json()
    assert done_payload["status"] == "answered"
    assert done_payload["lifecycle_state"] == "done"
    assert done_payload["has_answer"] is True


def test_job_bundle_export_contains_manifest_and_truncates(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"bundle"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-a"
    art.mkdir(parents=True, exist_ok=True)
    (art / "stdout.txt").write_text("x" * (300 * 1024), encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get("/jobs/job-a.json/bundle", headers=_operator_headers())

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")

    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(zf.namelist())
    assert "manifest.json" in names
    assert "job/job-a.json" in names
    assert "artifacts/stdout.txt" in names


def test_bundle_endpoints_require_auth(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-a.json").write_text('{"goal":"bundle"}', encoding="utf-8")
    (queue_dir / "failed" / "job-b.json").write_text('{"goal":"retry"}', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    assert client.get("/jobs/job-a.json/bundle").status_code == 401
    assert client.get("/bundle/system").status_code == 401

    no_csrf_cancel = client.post(
        "/queue/jobs/job-a.json/cancel",
        headers=_operator_headers(),
        data={},
        follow_redirects=False,
    )
    assert no_csrf_cancel.status_code == 403
    no_csrf_retry = client.post(
        "/queue/jobs/job-b.json/retry", headers=_operator_headers(), data={}, follow_redirects=False
    )
    assert no_csrf_retry.status_code == 403
    no_csrf_delete = client.post(
        "/queue/jobs/job-a.json/delete",
        headers=_operator_headers(),
        data={"confirm": "job-a.json"},
        follow_redirects=False,
    )
    assert no_csrf_delete.status_code == 403


def test_system_bundle_contains_manifest(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get("/bundle/system", headers=_operator_headers())
    assert res.status_code == 200

    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    assert "manifest.json" in set(zf.namelist())


def test_panel_shows_setup_required_banner_when_operator_password_missing(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.delenv("VOXERA_PANEL_OPERATOR_PASSWORD", raising=False)

    client = TestClient(panel_module.app)

    home_res = client.get("/")
    jobs_res = client.get("/jobs")

    assert home_res.status_code == 200
    assert jobs_res.status_code == 200
    assert "Setup required: panel operator password is not configured." in home_res.text
    assert "systemctl --user restart voxera-panel.service voxera-daemon.service" in home_res.text
    expected_config_path = str(panel_module._settings().config_path.expanduser())
    assert f"Config file: {expected_config_path}" in home_res.text
    assert "If VOXERA_LOAD_DOTENV=1, .env may override file settings." in home_res.text
    assert "Setup required: panel operator password is not configured." in jobs_res.text


def test_panel_hides_setup_required_banner_when_operator_password_set(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    home_res = client.get("/")
    jobs_res = client.get("/jobs")

    assert home_res.status_code == 200
    assert jobs_res.status_code == 200
    assert "Setup required: panel operator password is not configured." not in home_res.text
    assert "Setup required: panel operator password is not configured." not in jobs_res.text


def test_cancel_redirect_location_is_relative_for_proxy_safety(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "job-relative.json").write_text('{"goal":"x"}', encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-relative.json/cancel",
        data={"bucket": "pending", "q": "job", "n": "20"},
    )

    assert res.status_code == 303
    location = res.headers["location"]
    assert location.startswith("/jobs?")
    assert not location.startswith("http://")
    assert not location.startswith("https://")


def test_cancel_redirect_sanitizes_invalid_n_and_jobs_page_renders(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "job-n-invalid.json").write_text('{"goal":"x"}', encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-n-invalid.json/cancel",
        data={"bucket": "pending", "q": "job", "n": "not-an-int"},
    )

    assert res.status_code == 303
    assert "flash=canceled" in res.headers["location"]
    assert "n=80" in res.headers["location"]

    redirected = client.get(res.headers["location"])
    assert redirected.status_code == 200
    assert "Job moved to canceled/." in redirected.text


def test_cancel_moves_to_canceled_and_hidden_from_active_buckets(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "job-cancel.json").write_text('{"goal":"x"}', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-cancel.json/cancel",
        data={"bucket": "pending", "q": "job-cancel", "n": "20"},
    )
    assert res.status_code == 303
    assert "flash=canceled" in res.headers["location"]
    assert "bucket=pending" in res.headers["location"]

    assert (queue_dir / "canceled" / "job-cancel.json").exists()
    assert not (queue_dir / "inbox" / "job-cancel.json").exists()

    pending = client.get("/jobs", params={"bucket": "pending"})
    assert "job-cancel.json" not in pending.text
    canceled = client.get("/jobs", params={"bucket": "canceled"})
    assert "job-cancel.json" in canceled.text


def test_retry_from_failed_and_canceled_requeues_to_inbox(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "canceled").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "job-f.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "job-f.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "job-f.json",
                "error": "boom",
                "timestamp_ms": 1700000000000,
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "canceled" / "job-c.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    res_failed = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-f.json/retry",
        data={"bucket": "failed", "q": "job", "n": "10"},
    )
    assert res_failed.status_code == 303
    assert "flash=retried" in res_failed.headers["location"]

    res_canceled = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-c.json/retry",
        data={"bucket": "canceled", "q": "job", "n": "10"},
    )
    assert res_canceled.status_code == 303
    assert (queue_dir / "inbox" / "job-f.json").exists()
    assert (queue_dir / "inbox" / "job-c.json").exists()
    assert not (queue_dir / "failed" / "job-f.error.json").exists()


def test_delete_only_terminal_jobs_and_confirm_match(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ["done", "failed", "canceled", "inbox", "pending", "pending/approvals"]:
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-d.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "job-f.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "job-f.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "job-f.json",
                "error": "boom",
                "timestamp_ms": 1700000000000,
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "canceled" / "job-c.json").write_text("{}", encoding="utf-8")
    (queue_dir / "inbox" / "job-i.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "job-p.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "approvals" / "job-a.approval.json").write_text("{}", encoding="utf-8")
    art = queue_dir / "artifacts" / "job-d"
    art.mkdir(parents=True, exist_ok=True)
    (art / "stdout.txt").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    mismatch = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-d.json/delete",
        data={"confirm": "wrong.json", "bucket": "done", "q": "", "n": "20"},
    )
    assert mismatch.status_code == 400

    ok_done = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-d.json/delete",
        data={"confirm": "job-d.json", "bucket": "done", "q": "", "n": "20"},
    )
    assert ok_done.status_code == 303
    location = ok_done.headers["location"]
    assert location.startswith("/jobs?")
    assert not location.startswith("http://")
    assert not location.startswith("https://")
    assert not (queue_dir / "done" / "job-d.json").exists()
    assert not art.exists()

    for job in ["job-f.json", "job-c.json"]:
        ok = _authed_csrf_request(
            client,
            "post",
            f"/queue/jobs/{job}/delete",
            data={"confirm": job, "bucket": "all", "q": "", "n": "20"},
        )
        assert ok.status_code == 303

    for job in ["job-i.json", "job-p.json", "job-a.json"]:
        bad = _authed_csrf_request(
            client,
            "post",
            f"/queue/jobs/{job}/delete",
            data={"confirm": job, "bucket": "all", "q": "", "n": "20"},
        )
        assert bad.status_code == 404


def test_jobs_page_flash_rendered_from_redirect_param(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/jobs", params={"flash": "retried"})
    assert res.status_code == 200
    assert "Job re-enqueued into inbox/." in res.text


def test_cancel_failed_job_redirects_with_flash_instead_of_500(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "job-failed.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-failed.json/cancel",
        data={"bucket": "failed", "q": "job", "n": "25"},
    )

    assert res.status_code == 303
    assert "flash=cannot_cancel_terminal" in res.headers["location"]
    assert "bucket=failed" in res.headers["location"]


def test_cancel_missing_job_redirects_with_flash_instead_of_500(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/not-there.json/cancel",
        data={"bucket": "all", "q": "not-there", "n": "25"},
    )

    assert res.status_code == 303
    assert "flash=cancel_not_found" in res.headers["location"]


def test_jobs_failed_bucket_does_not_render_cancel_button(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "job-failed.json").write_text('{"goal":"f"}', encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/jobs", params={"bucket": "failed"})

    assert res.status_code == 200
    assert "/queue/jobs/job-failed.json/cancel" not in res.text
    assert "/queue/jobs/job-failed.json/retry" in res.text


def test_panel_approve_accepts_pending_json_variant_ref(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-variant.json").write_text(
        json.dumps({"mission_id": "system_check", "approval_required": True}), encoding="utf-8"
    )
    approval_path = queue_dir / "pending" / "approvals" / "job-variant.approval.json"
    approval_path.write_text(
        json.dumps({"job": "job-variant.json", "step": 0, "skill": "approval_required"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client, "post", "/queue/approvals/job-variant.pending.json/approve", data={}
    )

    assert res.status_code == 303
    location = res.headers.get("location", "")
    assert "flash=approved" in location
    assert "flash=approval_invalid" not in location
    assert not approval_path.exists()
    assert not (queue_dir / "pending" / "job-variant.json").exists()
    assert (queue_dir / "done" / "job-variant.json").exists() or (
        queue_dir / "failed" / "job-variant.json"
    ).exists()


def test_panel_approval_missing_ref_redirects_with_flash_instead_of_500(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    res = _authed_csrf_request(client, "post", "/queue/approvals/missing.json/approve", data={})

    assert res.status_code == 303
    assert "flash=approval_not_found" in res.headers.get("location", "")


def test_panel_auth_lockout_after_10_failures(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    now = {"ms": 1_700_000_000_000}
    monkeypatch.setattr(panel_module, "_now_ms", lambda: now["ms"])

    client = TestClient(panel_module.app)
    for _ in range(9):
        res = client.post(
            "/queue/create",
            data={"kind": "goal", "goal": "x"},
            headers=_operator_headers(password="wrong"),
        )
        assert res.status_code == 401
        now["ms"] += 1_000

    tenth = client.post(
        "/queue/create",
        data={"kind": "goal", "goal": "x"},
        headers=_operator_headers(password="wrong"),
    )
    assert tenth.status_code == 429
    assert tenth.headers.get("Retry-After") == "60"


def test_panel_auth_lockout_blocks_subsequent_requests(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    now = {"ms": 1_700_000_000_000}
    monkeypatch.setattr(panel_module, "_now_ms", lambda: now["ms"])

    client = TestClient(panel_module.app)
    for _ in range(10):
        client.post(
            "/queue/create",
            data={"kind": "goal", "goal": "x"},
            headers=_operator_headers(password="wrong"),
        )
        now["ms"] += 1_000

    blocked = client.post(
        "/queue/create",
        data={"kind": "goal", "goal": "x"},
        headers=_operator_headers(password="wrong"),
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") == "60"


def test_panel_auth_lockout_resets_after_window(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    now = {"ms": 1_700_000_000_000}
    monkeypatch.setattr(panel_module, "_now_ms", lambda: now["ms"])

    client = TestClient(panel_module.app)
    for _ in range(10):
        client.post(
            "/queue/create",
            data={"kind": "goal", "goal": "x"},
            headers=_operator_headers(password="wrong"),
        )
        now["ms"] += 1_000

    now["ms"] += 61_000
    after_lockout = client.post(
        "/queue/create",
        data={"kind": "goal", "goal": "x"},
        headers=_operator_headers(password="wrong"),
    )
    assert after_lockout.status_code == 401

    for _ in range(8):
        now["ms"] += 1_000
        res = client.post(
            "/queue/create",
            data={"kind": "goal", "goal": "x"},
            headers=_operator_headers(password="wrong"),
        )
        assert res.status_code == 401

    now["ms"] += 61_000
    reset_attempt = client.post(
        "/queue/create",
        data={"kind": "goal", "goal": "x"},
        headers=_operator_headers(password="wrong"),
    )
    assert reset_attempt.status_code == 401


def test_health_snapshot_contains_panel_auth_state(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    now = {"ms": 1_700_000_000_000}
    monkeypatch.setattr(panel_module, "_now_ms", lambda: now["ms"])

    client = TestClient(panel_module.app)
    for _ in range(10):
        client.post(
            "/queue/create",
            data={"kind": "goal", "goal": "x"},
            headers=_operator_headers(password="wrong"),
        )
        now["ms"] += 1_000

    health_path = fake_home / "VoxeraOS" / "notes" / "queue" / "health.json"
    payload = json.loads(health_path.read_text(encoding="utf-8"))
    panel_auth = payload.get("panel_auth", {})
    lockouts = panel_auth.get("lockouts_by_ip", {})
    assert lockouts
    row = next(iter(lockouts.values()))
    assert int(row.get("until_ts_ms", 0)) > now["ms"]


def test_hygiene_page_renders_with_no_results(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/hygiene", headers=_operator_headers())

    assert res.status_code == 200
    assert "Queue Hygiene" in res.text
    assert "No runs yet." in res.text
    assert "Prune state" in res.text
    assert "Reconcile state" in res.text


def test_hygiene_prune_dry_run_endpoint_writes_health(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 0
        stdout = json.dumps(
            {
                "status": "dry_run",
                "per_bucket": {"done": {"pruned": 2}, "failed": {"pruned": 1}},
                "reclaimed_bytes": 123,
            }
        )
        stderr = ""

    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):
        captured["cmd"] = args[0]
        captured["cwd"] = kwargs.get("cwd")
        return _Proc()

    monkeypatch.setattr(panel_module.subprocess, "run", _fake_run)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/prune-dry-run", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["result"]["mode"] == "dry-run"
    assert payload["result"]["would_remove_jobs"] == 3
    assert payload["result"]["ts_ms"] > 0
    assert captured["cmd"][0] == panel_module.sys.executable
    assert captured["cmd"][1:3] == ["-m", "voxera.cli"]
    assert "--dry-run" not in captured["cmd"]
    assert payload["result"]["cwd"] == str(captured["cwd"])

    health = json.loads((fake_home / "VoxeraOS" / "notes" / "queue" / "health.json").read_text())
    assert "last_prune_result" in health
    assert health["last_prune_result"]["would_remove_jobs"] == 3


def test_hygiene_reconcile_endpoint_writes_health(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 0
        stdout = json.dumps({"issue_counts": {"orphan_sidecars": 2, "duplicate_jobs": 1}})
        stderr = ""

    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):
        captured["cmd"] = args[0]
        captured["cwd"] = kwargs.get("cwd")
        return _Proc()

    monkeypatch.setattr(panel_module.subprocess, "run", _fake_run)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/reconcile", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["result"]["issue_counts"]["orphan_sidecars"] == 2
    assert payload["result"]["ts_ms"] > 0
    assert captured["cmd"][0] == panel_module.sys.executable
    assert captured["cmd"][1:3] == ["-m", "voxera.cli"]
    assert payload["result"]["cwd"] == str(captured["cwd"])

    health = json.loads((fake_home / "VoxeraOS" / "notes" / "queue" / "health.json").read_text())
    assert "last_reconcile_result" in health
    assert health["last_reconcile_result"]["issue_counts"]["duplicate_jobs"] == 1


def test_hygiene_endpoints_require_auth(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    assert client.post("/hygiene/prune-dry-run").status_code == 401
    assert client.post("/hygiene/reconcile").status_code == 401


def test_hygiene_prune_dry_run_failure_includes_debug_fields_and_writes_health(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "prune failed: missing config\nextra context"

    monkeypatch.setattr(panel_module.subprocess, "run", lambda *args, **kwargs: _Proc())

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/prune-dry-run", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["result"]["error"]
    assert payload["result"]["exit_code"] == 2
    assert "prune failed" in payload["result"]["stderr_tail"]

    health = json.loads((fake_home / "VoxeraOS" / "notes" / "queue" / "health.json").read_text())
    assert health["last_prune_result"]["exit_code"] == 2
    assert "prune failed" in health["last_prune_result"]["stderr_tail"]


def test_hygiene_reconcile_failure_includes_debug_fields_and_writes_health(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "reconcile failed: bad path"

    monkeypatch.setattr(panel_module.subprocess, "run", lambda *args, **kwargs: _Proc())

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/reconcile", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["result"]["error"]
    assert payload["result"]["exit_code"] == 1
    assert "reconcile failed" in payload["result"]["stderr_tail"]

    health = json.loads((fake_home / "VoxeraOS" / "notes" / "queue" / "health.json").read_text())
    assert health["last_reconcile_result"]["exit_code"] == 1
    assert "reconcile failed" in health["last_reconcile_result"]["stderr_tail"]


def test_hygiene_prune_rc0_empty_stdout_is_classified(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = "ran but no json"

    monkeypatch.setattr(panel_module.subprocess, "run", lambda *args, **kwargs: _Proc())

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/prune-dry-run", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["result"]["error"] == "no json output"
    assert payload["result"]["exit_code"] == 0
    assert payload["result"]["stderr_tail"] == "ran but no json"


def test_hygiene_reconcile_rc0_invalid_json_is_classified(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    class _Proc:
        returncode = 0
        stdout = "{not-json}"
        stderr = ""

    monkeypatch.setattr(panel_module.subprocess, "run", lambda *args, **kwargs: _Proc())

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/hygiene/reconcile", data={})

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["result"]["error"] == "json parse failed"
    assert payload["result"]["exit_code"] == 0
    assert payload["result"]["stdout_tail"] == "{not-json}"


def test_recovery_page_renders_empty_state(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/recovery")

    assert res.status_code == 200
    assert "No recovery sessions found." in res.text
    assert "No quarantine sessions found." in res.text
    assert "Recovery / Shutdown Context" in res.text


def test_recovery_page_lists_sessions_and_sizes(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "recovery" / "session-a").mkdir(parents=True, exist_ok=True)
    (queue_dir / "recovery" / "session-a" / "file1.txt").write_text("one", encoding="utf-8")
    (queue_dir / "recovery" / "session-a" / "file2.txt").write_text("two", encoding="utf-8")
    (queue_dir / "quarantine" / "session-q").mkdir(parents=True, exist_ok=True)
    (queue_dir / "quarantine" / "session-q" / "q1.txt").write_text("q", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    res = client.get("/recovery")

    assert res.status_code == 200
    assert "session-a" in res.text
    assert "session-q" in res.text
    assert "Download ZIP" in res.text


def test_download_zip_for_directory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    session = queue_dir / "recovery" / "session-a"
    session.mkdir(parents=True, exist_ok=True)
    (session / "a.txt").write_text("alpha", encoding="utf-8")
    (session / "b.txt").write_text("beta", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get("/recovery/download/recovery/session-a", headers=_operator_headers())

    assert res.status_code == 200
    assert res.headers.get("content-type", "").startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = sorted(zf.namelist())
    assert names == ["a.txt", "b.txt"]


def test_download_rejects_path_traversal(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)

    res_parent = client.get("/recovery/download/recovery/..", headers=_operator_headers())
    assert res_parent.status_code == 404

    res_missing = client.get("/recovery/download/recovery/not-found", headers=_operator_headers())
    assert res_missing.status_code == 404

    res_with_slash = client.get(
        "/recovery/download/recovery/session-a/extra", headers=_operator_headers()
    )
    assert res_with_slash.status_code == 404


def test_panel_security_snapshot_reads_same_default_root_as_counter_writes(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    panel_module._panel_security_counter_incr("panel_auth_invalid")

    counters = panel_module._panel_security_snapshot()

    assert counters["panel_auth_invalid"] == 1


def test_panel_security_snapshot_reads_same_isolated_root_as_counter_writes(tmp_path, monkeypatch):
    repo_root = tmp_path / "VoxeraOS"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(panel_module.Path, "home", lambda: tmp_path)

    real_queue_root = repo_root / "notes" / "queue"
    real_queue_root.mkdir(parents=True, exist_ok=True)
    (real_queue_root / "health.json").write_text(
        json.dumps({"counters": {"panel_auth_invalid": 7}}), encoding="utf-8"
    )

    isolated_health = tmp_path / "isolated" / "health.json"
    isolated_health.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(isolated_health))

    panel_module._panel_security_counter_incr("panel_auth_invalid")

    counters = panel_module._panel_security_snapshot()

    assert counters["panel_auth_invalid"] == 1
    assert (
        json.loads((real_queue_root / "health.json").read_text(encoding="utf-8"))["counters"][
            "panel_auth_invalid"
        ]
        == 7
    )


def test_panel_auth_failure_writes_to_isolated_health_path_when_config_uses_real_queue(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "VoxeraOS"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(panel_module.Path, "home", lambda: tmp_path)

    real_queue_root = repo_root / "notes" / "queue"
    real_queue_root.mkdir(parents=True, exist_ok=True)
    real_health = real_queue_root / "health.json"
    real_health.write_text("{}", encoding="utf-8")
    real_before = real_health.read_text(encoding="utf-8")

    isolated_health = tmp_path / "isolated" / "health.json"
    isolated_health.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(isolated_health))
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    unauth = client.post("/queue/create", data={"kind": "goal", "goal": "noop"})

    assert unauth.status_code == 401
    assert real_health.read_text(encoding="utf-8") == real_before

    isolated_payload = json.loads(isolated_health.read_text(encoding="utf-8"))
    assert isolated_payload["last_error"] == "missing authorization"
    assert isolated_payload["counters"]["panel_401_count"] >= 1


def test_hygiene_page_requires_admin_auth(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    assert client.get("/hygiene").status_code == 401
    authed = client.get("/hygiene", headers=_operator_headers())
    assert authed.status_code == 200
    assert "Health Reset" in authed.text


def test_panel_hygiene_health_reset_updates_snapshot_and_audits(tmp_path, monkeypatch):
    from voxera.health import read_health_snapshot, write_health_snapshot

    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    write_health_snapshot(
        queue_dir,
        {
            "daemon_state": "degraded",
            "consecutive_brain_failures": 4,
            "last_error": "boom",
            "counters": {"panel_401_count": 3},
        },
    )
    events: list[dict[str, object]] = []
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setattr(panel_module, "log", lambda e: events.append(e))

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/hygiene/health-reset",
        data={"scope": "current_and_recent"},
    )
    assert res.status_code == 303
    assert "flash=health_reset_current_and_recent" in (res.headers.get("location") or "")
    payload = read_health_snapshot(queue_dir)
    assert payload["consecutive_brain_failures"] == 0
    assert payload["last_error"] is None
    assert payload["counters"]["panel_401_count"] == 3
    assert any(e.get("event") == "health_reset_current_and_recent" for e in events)


def test_panel_hygiene_health_reset_uses_isolated_health_path(tmp_path, monkeypatch):
    from voxera.health import read_health_snapshot, write_health_snapshot

    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(tmp_path / "isolated" / "health.json"))
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    health_root = panel_module._health_queue_root()
    write_health_snapshot(
        health_root,
        {"last_error": "isolated err", "counters": {"panel_401_count": 5}},
    )

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/hygiene/health-reset",
        data={"scope": "recent_history"},
    )
    assert res.status_code == 303
    payload = read_health_snapshot(health_root)
    assert payload["last_error"] is None
    assert payload["counters"]["panel_401_count"] == 5


def test_hygiene_page_uses_url_for_action_paths(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app, root_path="/panel")
    res = client.get("/hygiene", headers=_operator_headers())

    assert res.status_code == 200
    assert "http://testserver/panel/hygiene/prune-dry-run" in res.text
    assert "http://testserver/panel/hygiene/reconcile" in res.text
    assert "http://testserver/panel/hygiene/health-reset" in res.text


def test_operator_assistant_page_requires_auth(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    unauth = client.get("/assistant")
    assert unauth.status_code == 401

    authed = client.get("/assistant", headers=_operator_headers())
    assert authed.status_code == 200
    assert "Operator Assistant" in authed.text
    assert "Ask Voxera" in authed.text
    assert "Example prompts" in authed.text


def test_operator_assistant_submit_renders_grounded_response(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-1.json").write_text('{"goal":"demo"}', encoding="utf-8")
    (queue_dir / "pending" / "approvals" / "job-1.approval.json").write_text(
        json.dumps(
            {
                "job": "job-1.json",
                "step": 1,
                "skill": "system.open_url",
                "capability": "apps.open",
                "reason": "needs approval",
                "policy_reason": "ask",
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "health.json").write_text(
        json.dumps({"daemon_state": "healthy"}), encoding="utf-8"
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "Why is this job waiting?"},
    )
    assert res.status_code == 303
    assert "request_id=" in (res.headers.get("location") or "")


def test_operator_assistant_submit_creates_queue_job(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "From inside Voxera, how does the system look?"},
    )
    assert res.status_code == 303
    queued = list((queue_dir / "inbox").glob("job-assistant-*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "assistant_question"
    assert payload["read_only"] is True
    assert payload["thread_id"].startswith("thread-")


def test_operator_assistant_question_required(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/assistant/ask", data={"question": ""})
    assert res.status_code == 200
    assert "Question is required." in res.text


def test_operator_assistant_is_read_only_no_queue_mutation(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    sentinel = queue_dir / "pending" / "job-sentinel.json"
    sentinel.write_text('{"goal":"sentinel"}', encoding="utf-8")

    res = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "What is happening right now?"},
    )
    assert res.status_code == 303
    assert sentinel.exists()
    queued = list((queue_dir / "inbox").glob("job-assistant-*.json"))
    assert len(queued) == 1
    assert not list((queue_dir / "done").glob("job-assistant-*.json"))
    assert not list((queue_dir / "failed").glob("job-assistant-*.json"))


def test_operator_assistant_page_shows_fallback_metadata(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-assistant-2.json").write_text(
        json.dumps({"thread_id": "thread-fb"}), encoding="utf-8"
    )
    (queue_dir / "artifacts" / "job-assistant-2").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts" / "job-assistant-2" / "assistant_response.json").write_text(
        json.dumps(
            {
                "thread_id": "thread-fb",
                "answer": "Recovered via fallback model.",
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

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get(
        "/assistant?request_id=job-assistant-2.json&thread_id=thread-fb",
        headers=_operator_headers(),
    )
    assert res.status_code == 200
    assert "Answered by:" in res.text
    assert "fallback after TIMEOUT" in res.text
    assert "Mode:" in res.text


def test_operator_assistant_degraded_mode_when_queue_unavailable(tmp_path, monkeypatch, recwarn):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(
        panel_module,
        "enqueue_assistant_question",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("queue unavailable")),
    )

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "What is happening right now?"},
    )
    assert res.status_code == 200
    assert "degraded_brain_only" in res.text
    assert "queue_unavailable" in res.text
    assert not any("was never awaited" in str(w.message) for w in recwarn)


def test_degraded_assistant_prefers_model_backed_primary(monkeypatch):
    monkeypatch.setattr(
        panel_module,
        "load_app_config",
        lambda: SimpleNamespace(
            brain={
                "primary": SimpleNamespace(
                    type="openai_compat",
                    model="model-primary",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
                "fallback": SimpleNamespace(
                    type="openai_compat",
                    model="model-fallback",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
            }
        ),
    )

    class _PrimaryBrain:
        async def generate(self, messages, tools=None):
            return SimpleNamespace(text="I see queue pending is low and approvals are clear.")

    monkeypatch.setattr(
        panel_module, "_create_panel_assistant_brain", lambda provider: _PrimaryBrain()
    )

    result = panel_module._generate_degraded_assistant_answer(
        "What is happening right now?",
        {"health_current_state": {"daemon_state": "healthy"}, "queue_counts": {"pending": 0}},
        thread_turns=[],
        degraded_reason="daemon_paused",
    )

    assert result["provider"] == "primary"
    assert result["deterministic_used"] is False
    assert "model-only recovery mode" in result["answer"]
    assert "read-only" in result["answer"]


def test_degraded_assistant_uses_fallback_model_when_primary_fails(monkeypatch):
    monkeypatch.setattr(
        panel_module,
        "load_app_config",
        lambda: SimpleNamespace(
            brain={
                "primary": SimpleNamespace(
                    type="openai_compat",
                    model="model-primary",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
                "fallback": SimpleNamespace(
                    type="openai_compat",
                    model="model-fallback",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
            }
        ),
    )

    class _PrimaryBrain:
        async def generate(self, messages, tools=None):
            raise TimeoutError("timed out")

    class _FallbackBrain:
        async def generate(self, messages, tools=None):
            return SimpleNamespace(text="Recovered answer with current runtime context.")

    monkeypatch.setattr(
        panel_module,
        "_create_panel_assistant_brain",
        lambda provider: _PrimaryBrain() if provider.model == "model-primary" else _FallbackBrain(),
    )

    result = panel_module._generate_degraded_assistant_answer(
        "What is happening right now?",
        {"health_current_state": {"daemon_state": "healthy"}, "queue_counts": {"pending": 1}},
        thread_turns=[],
        degraded_reason="advisory_transport_stalled",
    )

    assert result["provider"] == "fallback"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "TIMEOUT"
    assert result["deterministic_used"] is False


def test_degraded_assistant_uses_deterministic_only_after_model_failures(monkeypatch):
    monkeypatch.setattr(
        panel_module,
        "load_app_config",
        lambda: SimpleNamespace(
            brain={
                "primary": SimpleNamespace(
                    type="openai_compat",
                    model="model-primary",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
                "fallback": SimpleNamespace(
                    type="openai_compat",
                    model="model-fallback",
                    base_url="",
                    api_key_ref="",
                    extra_headers={},
                ),
            }
        ),
    )

    class _FailBrain:
        async def generate(self, messages, tools=None):
            raise TimeoutError("timed out")

    monkeypatch.setattr(
        panel_module, "_create_panel_assistant_brain", lambda provider: _FailBrain()
    )

    result = panel_module._generate_degraded_assistant_answer(
        "What is happening right now?",
        {"health_current_state": {"daemon_state": "healthy"}, "queue_counts": {"pending": 2}},
        thread_turns=[],
        degraded_reason="daemon_unavailable",
    )

    assert result["provider"] == "deterministic_fallback"
    assert result["deterministic_used"] is True
    assert result["fallback_reason"] == "TIMEOUT"


def test_operator_assistant_page_degrades_when_daemon_paused(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / ".paused").write_text("1", encoding="utf-8")
    (queue_dir / "pending" / "job-assistant-paused.json").write_text(
        json.dumps({"kind": "assistant_question", "thread_id": "thread-paused"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-assistant-paused.state.json").write_text(
        json.dumps({"lifecycle_state": "queued", "updated_at_ms": 1}), encoding="utf-8"
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get(
        "/assistant?request_id=job-assistant-paused.json&thread_id=thread-paused&question=What+is+happening+right+now%3F",
        headers=_operator_headers(),
    )
    assert res.status_code == 200
    assert "degraded_brain_only" in res.text
    assert "daemon_paused" in res.text


def test_operator_assistant_page_degrades_when_transport_stalled(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps({"daemon_state": "healthy"}), encoding="utf-8"
    )
    old_ts = 1
    (queue_dir / "pending" / "job-assistant-stale.json").write_text(
        json.dumps({"kind": "assistant_question", "thread_id": "thread-stale"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-assistant-stale.state.json").write_text(
        json.dumps({"lifecycle_state": "advisory_running", "updated_at_ms": old_ts}),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get(
        "/assistant?request_id=job-assistant-stale.json&thread_id=thread-stale&question=What+is+happening+right+now%3F",
        headers=_operator_headers(),
    )
    assert res.status_code == 200
    assert "degraded_brain_only" in res.text
    assert "queue_processing_timeout" in res.text


def test_operator_assistant_page_does_not_degrade_when_request_is_progressing(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    now_ms = int(__import__("time").time() * 1000)
    (queue_dir / "health.json").write_text(
        json.dumps({"daemon_state": "healthy"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-assistant-active.json").write_text(
        json.dumps({"kind": "assistant_question", "thread_id": "thread-active"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-assistant-active.state.json").write_text(
        json.dumps({"lifecycle_state": "advisory_running", "updated_at_ms": now_ms}),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get(
        "/assistant?request_id=job-assistant-active.json&thread_id=thread-active&question=What+is+happening+right+now%3F",
        headers=_operator_headers(),
    )
    assert res.status_code == 200
    assert "thinking through Voxera" in res.text
    assert "degraded_brain_only" not in res.text


def test_operator_assistant_page_shows_completed_queue_answer(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-assistant-1.json").write_text(
        json.dumps({"thread_id": "thread-abc"}), encoding="utf-8"
    )
    (queue_dir / "artifacts" / "job-assistant-1").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts" / "job-assistant-1" / "assistant_response.json").write_text(
        json.dumps(
            {
                "thread_id": "thread-abc",
                "answer": "Control-plane view: pending=0.",
                "updated_at_ms": 1,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    res = client.get(
        "/assistant?request_id=job-assistant-1.json&thread_id=thread-abc&question=What+is+happening+right+now%3F",
        headers=_operator_headers(),
    )
    assert res.status_code == 200
    assert "answered" in res.text
    assert "Control-plane view: pending=0." in res.text
    assert "Thread:" in res.text


def test_operator_assistant_followup_uses_same_thread(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")

    client = TestClient(panel_module.app)
    first = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "What is happening right now?", "thread_id": ""},
    )
    assert first.status_code == 303
    location = first.headers.get("location") or ""
    assert "thread_id=" in location

    from urllib.parse import parse_qs, urlparse

    thread_id = parse_qs(urlparse(location).query).get("thread_id", [""])[0]
    second = _authed_csrf_request(
        client,
        "post",
        "/assistant/ask",
        data={"question": "go on", "thread_id": thread_id},
    )
    assert second.status_code == 303
    second_loc = second.headers.get("location") or ""
    second_thread = parse_qs(urlparse(second_loc).query).get("thread_id", [""])[0]
    assert second_thread == thread_id

    queued = list((queue_dir / "inbox").glob("job-assistant-*.json"))
    assert len(queued) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in queued]
    assert all(item.get("thread_id") == thread_id for item in payloads)
