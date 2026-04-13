"""Focused tests for panel table usability improvements.

Pins: sticky-header scroll classes, right-aligned count cells,
consistent empty-state containers, and CSS class presence.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from voxera.panel import app as panel_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _home_body(tmp_path, monkeypatch, *, with_failed: bool = False):
    """Return the home page HTML body with a minimal queue scaffold."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")

    if with_failed:
        (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
        (queue_dir / "failed" / "job-err.json").write_text('{"goal":"err"}', encoding="utf-8")
        (queue_dir / "failed" / "job-err.error.json").write_text(
            json.dumps({"job": "job-err.json", "error": "boom", "ts": 1}),
            encoding="utf-8",
        )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    return client.get("/").text


def _jobs_body(tmp_path, monkeypatch, *, bucket: str = "inbox"):
    """Return the jobs page HTML body."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    return client.get("/jobs", params={"bucket": bucket}).text


# ---------------------------------------------------------------------------
# Sticky header / table-scroll tests
# ---------------------------------------------------------------------------


def test_home_approval_table_has_sticky_scroll(tmp_path, monkeypatch):
    """Approval Command Center table should have the table-scroll class
    for sticky headers when there are pending approvals."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-ask.json").write_text('{"goal":"ask"}', encoding="utf-8")
    (queue_dir / "pending" / "job-ask.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "ask"},
                "resume_step": 1,
                "mission": {
                    "id": "x",
                    "title": "X",
                    "goal": "x",
                    "steps": [{"skill_id": "system.status", "args": {}}],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-ask.approval.json").write_text(
        json.dumps(
            {
                "job": "job-ask.json",
                "step": 1,
                "skill": "system.open_url",
                "reason": "needs approval",
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    body = TestClient(panel_module.app).get("/").text
    # The approval table wrapper should have table-scroll
    idx = body.index("Approval Command Center")
    snippet = body[idx : idx + 600]
    assert "table-scroll" in snippet


def test_home_completed_jobs_table_has_sticky_scroll(tmp_path, monkeypatch):
    """Completed Jobs table should have sticky-scroll when jobs are present."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-done.json").write_text('{"goal":"done"}', encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    body = TestClient(panel_module.app).get("/").text
    idx = body.index("Completed Jobs")
    snippet = body[idx : idx + 400]
    assert "table-scroll" in snippet


def test_jobs_page_table_has_sticky_scroll(tmp_path, monkeypatch):
    """The main Job Browser table should have table-scroll for sticky headers."""
    body = _jobs_body(tmp_path, monkeypatch)
    assert "table-scroll" in body


# ---------------------------------------------------------------------------
# Empty state tests
# ---------------------------------------------------------------------------


def test_home_empty_active_work_renders_empty_state(tmp_path, monkeypatch):
    """With no active jobs the Active Work card should use .empty-state."""
    body = _home_body(tmp_path, monkeypatch)
    assert "No active work" in body
    idx = body.index("No active work")
    # The message should be inside an empty-state container
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_home_empty_completed_jobs_renders_empty_state(tmp_path, monkeypatch):
    """With no completed jobs the History section should show a styled empty state."""
    body = _home_body(tmp_path, monkeypatch)
    assert "No completed jobs yet" in body
    idx = body.index("No completed jobs yet")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_home_empty_mission_library_renders_empty_state(tmp_path, monkeypatch):
    """With no missions the Mission Library should show a styled empty state."""
    from voxera.panel import routes_home

    monkeypatch.setattr(routes_home, "list_missions", lambda: [])
    body = _home_body(tmp_path, monkeypatch)
    assert "No mission templates defined" in body
    idx = body.index("No mission templates defined")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_home_empty_approvals_renders_empty_state(tmp_path, monkeypatch):
    """With no pending approvals the Approval section should show empty state."""
    body = _home_body(tmp_path, monkeypatch)
    assert "No pending queue approvals" in body
    idx = body.index("No pending queue approvals")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_home_empty_failed_jobs_renders_empty_state(tmp_path, monkeypatch):
    """With no failed jobs the Failed Jobs card should show empty state."""
    body = _home_body(tmp_path, monkeypatch)
    assert "No failed jobs" in body
    idx = body.index("No failed jobs")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_home_empty_audit_renders_empty_state(tmp_path, monkeypatch):
    """With no audit events the Audit section should use .empty-state."""
    from voxera.panel import routes_home

    monkeypatch.setattr(routes_home, "tail", lambda n=50: [])
    body = _home_body(tmp_path, monkeypatch)
    assert "No audit events yet" in body
    idx = body.index("No audit events yet")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


def test_jobs_page_empty_renders_empty_state(tmp_path, monkeypatch):
    """With no matching jobs the Job Browser should show empty state."""
    body = _jobs_body(tmp_path, monkeypatch)
    assert "No jobs found" in body
    idx = body.index("No jobs found")
    start = max(0, idx - 200)
    snippet = body[start : idx + 50]
    assert "empty-state" in snippet


# ---------------------------------------------------------------------------
# Count cell alignment tests
# ---------------------------------------------------------------------------


def test_home_daemon_lock_history_count_cells_aligned(tmp_path, monkeypatch):
    """Daemon Lock History count column should have cell-count for right-alignment."""
    body = _home_body(tmp_path, monkeypatch)
    idx = body.index("Daemon Lock History")
    # Scan the section for cell-count class
    snippet = body[idx : idx + 2000]
    assert "cell-count" in snippet


def test_home_security_counters_count_cells_aligned(tmp_path, monkeypatch):
    """Panel Security Counters count column should have cell-count for right-alignment."""
    body = _home_body(tmp_path, monkeypatch)
    idx = body.index("Panel Security Counters")
    snippet = body[idx : idx + 2000]
    assert "cell-count" in snippet


def test_home_mission_library_steps_count_aligned(tmp_path, monkeypatch):
    """Mission Library steps column should use cell-count when missions exist."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    # Create a mission template so the table renders
    missions_dir = fake_home / "VoxeraOS" / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    body = TestClient(panel_module.app).get("/").text
    # Even if no file-based missions load, the built-in catalog populates the table.
    # The steps column for each mission row should have cell-count.
    idx = body.index("Mission Library")
    snippet = body[idx : idx + 3000]
    assert "cell-count" in snippet


# ---------------------------------------------------------------------------
# CSS class presence in static file
# ---------------------------------------------------------------------------


def test_panel_css_has_table_scroll_rules(tmp_path, monkeypatch):
    """The panel CSS static file should contain the table-scroll rules."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    css = client.get("/static/panel.css").text
    assert ".table-scroll" in css
    assert "position: sticky" in css
    assert ".cell-count" in css
    assert ".empty-state" in css
    assert ".lifecycle-cell" in css
    assert ".step-progress" in css
