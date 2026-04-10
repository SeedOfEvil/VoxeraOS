"""Tests for the panel automation dashboard routes.

Coverage:
1. Automations list page renders saved definitions
2. Automation detail page renders one saved definition
3. Enable route flips enabled to true and persists
4. Disable route flips enabled to false and persists
5. History appears on the detail page when present
6. Missing automation id returns a clean redirect
7. Malformed definition/history files are handled safely
8. Run-now goes through the normal automation runner / queue path
9. Run-now does not bypass the queue
10. Auth / mutation guard behavior remains consistent with existing panel norms
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from voxera.automation.history import build_history_record, write_history_record
from voxera.automation.models import AutomationDefinition
from voxera.automation.store import (
    ensure_automation_dirs,
    load_automation_definition,
    save_automation_definition,
)
from voxera.panel import app as panel_module


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(
    client: TestClient, method: str, url: str, *, data: dict[str, str] | None = None
):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data or {})
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


def _setup_queue(tmp_path: Path, monkeypatch, *, with_auth: bool = False) -> Path:
    """Create a minimal queue root and patch panel to use it."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    if with_auth:
        monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    ensure_automation_dirs(queue_dir)
    return queue_dir


def _make_definition(queue_root: Path, **overrides) -> AutomationDefinition:
    """Create and persist a valid automation definition."""
    base = {
        "id": "test-auto-1",
        "title": "Test Automation",
        "description": "a test automation",
        "trigger_kind": "once_at",
        "trigger_config": {"run_at_ms": 1_700_000_000_000},
        "payload_template": {"goal": "run test task"},
        "created_at_ms": 1_699_999_000_000,
        "updated_at_ms": 1_699_999_000_000,
        "created_from": "cli",
    }
    base.update(overrides)
    defn = AutomationDefinition(**base)
    save_automation_definition(defn, queue_root, touch_updated=False)
    return defn


# -----------------------------------------------------------------------
# 1. List page renders saved definitions
# -----------------------------------------------------------------------


def test_automations_list_renders_definitions(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="alpha", title="Alpha Task")
    _make_definition(queue_dir, id="beta", title="Beta Task", enabled=False)

    client = TestClient(panel_module.app)
    res = client.get("/automations")

    assert res.status_code == 200
    body = res.text
    assert "Automations" in body
    assert "alpha" in body
    assert "Alpha Task" in body
    assert "beta" in body
    assert "Beta Task" in body
    assert "once_at" in body


def test_automations_list_empty(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch)

    client = TestClient(panel_module.app)
    res = client.get("/automations")

    assert res.status_code == 200
    assert "No automation definitions found" in res.text


# -----------------------------------------------------------------------
# 2. Detail page renders one saved definition
# -----------------------------------------------------------------------


def test_automation_detail_renders_definition(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(
        queue_dir,
        id="detail-test",
        title="Detail Test",
        description="some description",
    )

    client = TestClient(panel_module.app)
    res = client.get("/automations/detail-test")

    assert res.status_code == 200
    body = res.text
    assert "detail-test" in body
    assert "Detail Test" in body
    assert "some description" in body
    assert "once_at" in body
    assert "run test task" in body
    assert "Run History" in body


# -----------------------------------------------------------------------
# 3. Enable route flips enabled to true and persists
# -----------------------------------------------------------------------


def test_enable_flips_to_true(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="en-test", enabled=False)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/en-test/enable")

    assert res.status_code == 303
    assert "/automations/en-test" in res.headers["location"]
    assert "flash=enabled" in res.headers["location"]

    reloaded = load_automation_definition("en-test", queue_dir)
    assert reloaded.enabled is True


def test_enable_already_enabled_redirects(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="en-already", enabled=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/en-already/enable")

    assert res.status_code == 303
    assert "flash=already_enabled" in res.headers["location"]


# -----------------------------------------------------------------------
# 4. Disable route flips enabled to false and persists
# -----------------------------------------------------------------------


def test_disable_flips_to_false(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="dis-test", enabled=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/dis-test/disable")

    assert res.status_code == 303
    assert "/automations/dis-test" in res.headers["location"]
    assert "flash=disabled" in res.headers["location"]

    reloaded = load_automation_definition("dis-test", queue_dir)
    assert reloaded.enabled is False


def test_disable_already_disabled_redirects(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="dis-already", enabled=False)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/dis-already/disable")

    assert res.status_code == 303
    assert "flash=already_disabled" in res.headers["location"]


# -----------------------------------------------------------------------
# 5. History appears on the detail page when present
# -----------------------------------------------------------------------


def test_detail_shows_history(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="hist-test")

    record = build_history_record(
        automation_id="hist-test",
        run_id="1700000000000-abc12345",
        triggered_at_ms=1_700_000_000_000,
        trigger_kind="once_at",
        outcome="submitted",
        queue_job_ref="inbox-job-1.json",
        message="due (anchor_ms=1700000000000, now_ms=1700000000000)",
        payload_template={"goal": "run test task"},
    )
    write_history_record(queue_dir, record)

    client = TestClient(panel_module.app)
    res = client.get("/automations/hist-test")

    assert res.status_code == 200
    body = res.text
    assert "1700000000000-abc12345" in body
    assert "submitted" in body
    assert "inbox-job-1.json" in body


# -----------------------------------------------------------------------
# 6. Missing automation id returns a clean response
# -----------------------------------------------------------------------


def test_detail_missing_automation_redirects(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch)

    client = TestClient(panel_module.app)
    res = client.get("/automations/does-not-exist", follow_redirects=False)

    assert res.status_code == 303
    assert "/automations" in res.headers["location"]
    assert "flash=not_found" in res.headers["location"]


def test_enable_missing_automation_redirects(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch, with_auth=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/nonexistent/enable")

    assert res.status_code == 303
    assert "flash=not_found" in res.headers["location"]


def test_disable_missing_automation_redirects(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch, with_auth=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/nonexistent/disable")

    assert res.status_code == 303
    assert "flash=not_found" in res.headers["location"]


def test_run_now_missing_automation_redirects(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch, with_auth=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/nonexistent/run-now")

    assert res.status_code == 303
    assert "flash=not_found" in res.headers["location"]


# -----------------------------------------------------------------------
# 7. Malformed definition/history files are handled safely
# -----------------------------------------------------------------------


def test_list_survives_malformed_definition(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="good-one", title="Good One")

    # Write a malformed JSON file into definitions
    from voxera.automation.store import definitions_dir

    bad_file = definitions_dir(queue_dir) / "bad-file.json"
    bad_file.write_text("{invalid json", encoding="utf-8")

    client = TestClient(panel_module.app)
    res = client.get("/automations")

    assert res.status_code == 200
    body = res.text
    assert "good-one" in body
    assert "Good One" in body
    # Malformed file is silently skipped
    assert "bad-file" not in body


def test_detail_survives_malformed_history(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="hist-bad")

    # Write a malformed history file
    from voxera.automation.store import history_dir

    bad_hist = history_dir(queue_dir) / "auto-hist-bad-9999.json"
    bad_hist.write_text("not-json", encoding="utf-8")

    client = TestClient(panel_module.app)
    res = client.get("/automations/hist-bad")

    assert res.status_code == 200
    # Page renders without error; malformed history is skipped
    assert "hist-bad" in body if (body := res.text) else True


# -----------------------------------------------------------------------
# 8. Run-now goes through normal runner / queue path
# -----------------------------------------------------------------------


def test_run_now_submits_through_runner(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="run-test", enabled=True)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/run-test/run-now")

    assert res.status_code == 303
    assert "/automations/run-test" in res.headers["location"]
    assert "flash=run_submitted" in res.headers["location"]

    # Verify an inbox file was created (queue submission, not direct execution)
    inbox_files = list((queue_dir / "inbox").glob("*.json"))
    assert len(inbox_files) >= 1

    # Verify the inbox payload matches the definition's payload_template
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload.get("goal") == "run test task"


# -----------------------------------------------------------------------
# 9. Run-now does not bypass the queue
# -----------------------------------------------------------------------


def test_run_now_creates_inbox_file_not_direct_execution(tmp_path, monkeypatch):
    """Ensure run-now writes to inbox/ (queue submission) and never writes
    to pending/, done/, or failed/ directly."""
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    (queue_dir / "pending").mkdir(exist_ok=True)
    (queue_dir / "done").mkdir(exist_ok=True)
    (queue_dir / "failed").mkdir(exist_ok=True)

    pending_before = list((queue_dir / "pending").glob("*.json"))
    done_before = list((queue_dir / "done").glob("*.json"))
    failed_before = list((queue_dir / "failed").glob("*.json"))

    _make_definition(queue_dir, id="queue-test", enabled=True)

    client = TestClient(panel_module.app)
    _authed_csrf_request(client, "post", "/automations/queue-test/run-now")

    # Inbox has a new file
    inbox_files = list((queue_dir / "inbox").glob("*.json"))
    assert len(inbox_files) >= 1

    # No direct writes to pending/done/failed
    assert list((queue_dir / "pending").glob("*.json")) == pending_before
    assert list((queue_dir / "done").glob("*.json")) == done_before
    assert list((queue_dir / "failed").glob("*.json")) == failed_before


def test_run_now_disabled_automation_skipped(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch, with_auth=True)
    _make_definition(queue_dir, id="disabled-run", enabled=False)

    client = TestClient(panel_module.app)
    res = _authed_csrf_request(client, "post", "/automations/disabled-run/run-now")

    assert res.status_code == 303
    assert "flash=run_skipped" in res.headers["location"]

    # No inbox file created
    inbox_files = list((queue_dir / "inbox").glob("*.json"))
    assert len(inbox_files) == 0


# -----------------------------------------------------------------------
# 10. Auth / mutation guard behavior
# -----------------------------------------------------------------------


def test_enable_without_auth_returns_401(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="auth-test")

    client = TestClient(panel_module.app, raise_server_exceptions=False)
    res = client.post("/automations/auth-test/enable")

    # Should require auth — 401 or 503 depending on password config
    assert res.status_code in {401, 503}


def test_disable_without_auth_returns_401(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="auth-test2")

    client = TestClient(panel_module.app, raise_server_exceptions=False)
    res = client.post("/automations/auth-test2/disable")

    assert res.status_code in {401, 503}


def test_run_now_without_auth_returns_401(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="auth-test3")

    client = TestClient(panel_module.app, raise_server_exceptions=False)
    res = client.post("/automations/auth-test3/run-now")

    assert res.status_code in {401, 503}


# -----------------------------------------------------------------------
# Flash messages
# -----------------------------------------------------------------------


def test_flash_messages_render_on_list_page(tmp_path, monkeypatch):
    _setup_queue(tmp_path, monkeypatch)

    client = TestClient(panel_module.app)
    res = client.get("/automations?flash=not_found")

    assert res.status_code == 200
    assert "Automation not found" in res.text


def test_flash_messages_render_on_detail_page(tmp_path, monkeypatch):
    queue_dir = _setup_queue(tmp_path, monkeypatch)
    _make_definition(queue_dir, id="flash-test")

    client = TestClient(panel_module.app)
    res = client.get("/automations/flash-test?flash=enabled")

    assert res.status_code == 200
    assert "Automation enabled" in res.text
