from __future__ import annotations

import base64
import json

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
    assert "Mission Log (last 20 lines)" in body
    assert "line-29" in body
    assert "line-8" not in body


def test_create_mission_modes_render_expected_fields(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    easy = client.get("/", params={"mission_mode": "easy"})
    assert easy.status_code == 200
    assert "Brain" not in easy.text
    assert "Priority" not in easy.text
    assert "Dry run" not in easy.text

    advanced = client.get("/", params={"mission_mode": "advanced"})
    assert advanced.status_code == 200
    assert "Brain" in advanced.text
    assert "Priority" in advanced.text
    assert "Dry run" in advanced.text


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
    assert payload == {"goal": "run system check"}

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
        "/missions/create",
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

    mission_res = client.get("/missions/create", follow_redirects=False)
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
        "/missions/create",
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
        client, "post", "/missions/create", data={"mission_id": "custom_status", "steps_json": "{"}
    )
    assert bad_json.status_code == 303
    assert bad_json.headers["location"] == "/?error=steps_json_invalid"

    not_list = _authed_csrf_request(
        client,
        "post",
        "/missions/create",
        data={"mission_id": "custom_status", "steps_json": '{"skill_id":"system.status"}'},
    )
    assert not_list.status_code == 303
    assert not_list.headers["location"] == "/?error=steps_json_not_list"

    schema_invalid = _authed_csrf_request(
        client,
        "post",
        "/missions/create",
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
            "/missions/create",
            data={
                "mission_id": mission_id,
                "steps_json": '[{"skill_id":"system.status","args":{}}]',
            },
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


def test_panel_create_mission_job_contract_and_auth(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    unauth = client.post("/queue/create", data={"prompt": "x", "mode": "easy"})
    assert unauth.status_code == 401

    auth_only = client.post(
        "/queue/create", data={"prompt": "x", "mode": "easy"}, headers=_operator_headers()
    )
    assert auth_only.status_code == 403

    ok = _authed_csrf_request(
        client,
        "post",
        "/queue/create",
        data={
            "mode": "advanced",
            "prompt": "check backups and report",
            "mission_id": "ops_backup",
            "title": "Backups audit",
            "approval_required": "on",
            "tags": "ops,nightly",
            "priority": "high",
            "brain": "reasoning",
            "target": "host:db-01",
            "scope": "workspace_only",
            "dry_run": "on",
        },
    )
    assert ok.status_code == 303

    queued = sorted((fake_home / "VoxeraOS" / "notes" / "queue" / "inbox").glob("*.json"))
    assert queued
    payload = json.loads(queued[-1].read_text(encoding="utf-8"))
    assert payload["job_version"] == 1
    assert payload["source"] == "panel"
    assert payload["prompt"] == "check backups and report"
    assert payload["approval_required"] is True
    assert payload["title"] == "Backups audit"
    assert payload["tags"] == ["ops", "nightly"]
    assert payload["priority"] == "high"
    assert payload["brain"] == "reasoning"
    assert payload["target"] == "host:db-01"
    assert payload["dry_run"] is True
    assert "created_ts_ms" in payload


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


def test_jobs_mutations_preserve_filters_and_show_flash(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "job-a.json").write_text('{"goal":"x"}', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    cancel = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-a.json/cancel?bucket=inbox&q=job-a&n=7",
        data={},
    )
    assert cancel.status_code == 303
    assert "bucket=inbox" in cancel.headers["location"]
    assert "q=job-a" in cancel.headers["location"]
    assert "n=7" in cancel.headers["location"]
    assert (queue_dir / "canceled" / "job-a.json").exists()

    jobs = client.get(cancel.headers["location"])
    assert "canceled:job-a.json" in jobs.text


def test_jobs_cancel_retry_approve_and_delete_guards(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-approval.json").write_text('{"goal":"g"}', encoding="utf-8")
    (queue_dir / "pending" / "job-approval.pending.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "approvals" / "job-approval.approval.json").write_text(
        json.dumps({"job": "job-approval.json", "reason": "needs approval"}), encoding="utf-8"
    )
    (queue_dir / "failed" / "job-failed.json").write_text('{"goal":"x"}', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    client = TestClient(panel_module.app)

    approve = _authed_csrf_request(
        client,
        "post",
        "/queue/approvals/job-approval.json/approve?bucket=approvals",
        data={},
    )
    assert approve.status_code == 303

    retry = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-failed.json/retry?bucket=failed",
        data={},
    )
    assert retry.status_code == 303
    assert (queue_dir / "inbox" / "job-failed.json").exists()

    done_dir = queue_dir / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / "job-done.json").write_text('{"goal":"done"}', encoding="utf-8")
    bad_delete = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-done.json/delete?bucket=done",
        data={"confirm_job_id": "wrong.json"},
    )
    assert bad_delete.status_code == 303
    assert "delete_confirm_mismatch" in bad_delete.headers["location"]

    good_delete = _authed_csrf_request(
        client,
        "post",
        "/queue/jobs/job-done.json/delete?bucket=done",
        data={"confirm_job_id": "job-done.json"},
    )
    assert good_delete.status_code == 303
    assert not (done_dir / "job-done.json").exists()


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
