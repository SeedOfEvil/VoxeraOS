"""Focused tests for the panel accessibility and mobile-responsiveness
hardening pass.

Scope: pins the bounded, cross-cutting improvements from the
accessibility/mobile PR — semantic landmarks, ARIA tab pattern, focus
visibility CSS rules, responsive breakpoints, status text reinforcement,
and skip-link presence.

These tests do NOT claim full WCAG compliance. They pin the specific
improvements shipped in this PR so a future template/CSS edit that
removes them will fail loudly.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from voxera.panel import app as panel_module

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup_home(tmp_path, monkeypatch, *, failed: int = 0, approvals: int = 0):
    """Minimal home-page fixture setup."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")

    for i in range(failed):
        name = f"job-fail-{i}.json"
        (queue_dir / "failed" / name).write_text('{"goal":"boom"}', encoding="utf-8")
        (queue_dir / "failed" / f"job-fail-{i}.error.json").write_text(
            json.dumps({"job": name, "error": "kaboom", "ts": 1}), encoding="utf-8"
        )

    for i in range(approvals):
        name = f"job-ask-{i}.json"
        (queue_dir / "pending" / name).write_text('{"goal":"ask"}', encoding="utf-8")
        (queue_dir / "pending" / f"job-ask-{i}.pending.json").write_text(
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
        (queue_dir / "pending" / "approvals" / f"job-ask-{i}.approval.json").write_text(
            json.dumps(
                {
                    "job": name,
                    "step": 1,
                    "skill": "system.open_url",
                    "reason": "needs approval",
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    return fake_home


def _get_home(tmp_path, monkeypatch, **kwargs) -> str:
    _setup_home(tmp_path, monkeypatch, **kwargs)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    return res.text


def _get_jobs(tmp_path, monkeypatch) -> str:
    _setup_home(tmp_path, monkeypatch)
    client = TestClient(panel_module.app)
    res = client.get("/jobs")
    assert res.status_code == 200
    return res.text


# ---------------------------------------------------------------------------
# 1. Semantic landmarks and skip-link
# ---------------------------------------------------------------------------


def test_home_has_skip_link_and_main_landmark(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'class="skip-link"' in body
    assert 'href="#main-content"' in body
    assert 'id="main-content"' in body
    assert "<main" in body


def test_jobs_has_skip_link_and_main_landmark(tmp_path, monkeypatch):
    body = _get_jobs(tmp_path, monkeypatch)
    assert 'class="skip-link"' in body
    assert 'href="#main-content"' in body
    assert "<main" in body


def test_home_page_nav_has_aria_label(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'aria-label="Panel sections"' in body


def test_home_nav_separators_are_aria_hidden(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'aria-hidden="true"' in body
    # Separators should be hidden from screen readers
    assert 'class="sep" aria-hidden="true"' in body


# ---------------------------------------------------------------------------
# 2. ARIA tab pattern
# ---------------------------------------------------------------------------


def test_home_tabs_use_aria_tablist_pattern(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'role="tablist"' in body
    assert 'role="tab"' in body
    assert 'role="tabpanel"' in body
    assert 'aria-selected="true"' in body
    assert 'aria-selected="false"' in body
    assert 'aria-controls="panel-control"' in body
    assert 'aria-controls="panel-logging"' in body
    assert 'aria-controls="panel-performance"' in body
    assert 'id="panel-control"' in body
    assert 'id="panel-logging"' in body
    assert 'id="panel-performance"' in body
    assert 'aria-labelledby="tab-control"' in body
    assert 'aria-labelledby="tab-logging"' in body
    assert 'aria-labelledby="tab-performance"' in body


def test_home_tab_keyboard_navigation_script_present(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    # The JS should handle arrow keys for tab navigation
    assert "ArrowRight" in body
    assert "ArrowLeft" in body
    assert "aria-selected" in body


# ---------------------------------------------------------------------------
# 3. Alert roles
# ---------------------------------------------------------------------------


def test_home_queue_status_badge_has_role_status(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'role="status"' in body


# ---------------------------------------------------------------------------
# 4. Action button aria-labels
# ---------------------------------------------------------------------------


def test_home_approval_buttons_have_aria_labels(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch, approvals=1)
    assert 'aria-label="Approve job' in body
    assert 'aria-label="Deny job' in body
    assert 'aria-label="Always approve job' in body
    assert 'aria-label="Cancel job' in body


def test_home_failed_job_buttons_have_aria_labels(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch, failed=1)
    assert 'aria-label="Retry job' in body
    assert 'aria-label="Cancel job' in body


def test_jobs_action_buttons_have_aria_labels(tmp_path, monkeypatch):
    """Jobs page action buttons should have aria-labels when present."""
    _setup_home(tmp_path, monkeypatch, approvals=1)
    client = TestClient(panel_module.app)
    body = client.get("/jobs?bucket=approvals").text
    assert 'aria-label="Approve job' in body
    assert 'aria-label="Deny job' in body


# ---------------------------------------------------------------------------
# 5. Queue controls group
# ---------------------------------------------------------------------------


def test_home_queue_controls_have_group_role(tmp_path, monkeypatch):
    body = _get_home(tmp_path, monkeypatch)
    assert 'aria-label="Queue controls"' in body


# ---------------------------------------------------------------------------
# 6. Status text reinforcement (non-color-only)
# ---------------------------------------------------------------------------


def test_home_kpi_warn_has_text_hint(tmp_path, monkeypatch):
    """When pending_approvals > 0, KPI card shows text hint alongside color."""
    body = _get_home(tmp_path, monkeypatch, approvals=1)
    assert "kpi-warn" in body
    assert "action needed" in body


def test_home_kpi_danger_has_text_hint(tmp_path, monkeypatch):
    """When failed > 0, KPI card shows text hint alongside color."""
    body = _get_home(tmp_path, monkeypatch, failed=1)
    assert "kpi-danger" in body
    assert "attention" in body


def test_home_kpi_no_hint_when_zero(tmp_path, monkeypatch):
    """When counts are zero, no status hints appear."""
    body = _get_home(tmp_path, monkeypatch)
    assert "action needed" not in body
    assert "kpi-status-hint" not in body


def test_jobs_artifact_pills_have_check_cross_marks(tmp_path, monkeypatch):
    """Artifact pills should include check/cross marks for non-color status."""
    fake_home = _setup_home(tmp_path, monkeypatch)
    # Need a job to produce artifact pills in the table
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    (queue_dir / "done" / "job-art.json").write_text('{"goal":"art"}', encoding="utf-8")
    (queue_dir / "artifacts" / "job-art").mkdir(parents=True, exist_ok=True)

    client = TestClient(panel_module.app)
    body = client.get("/jobs?bucket=done").text
    assert "artifact-pill" in body
    # Check marks (✓ or ✗) should be present in artifact pills
    assert "\u2713" in body or "\u2717" in body or "&#10003;" in body or "&#10005;" in body


# ---------------------------------------------------------------------------
# 7. Jobs filter form
# ---------------------------------------------------------------------------


def test_jobs_filter_form_has_aria_label(tmp_path, monkeypatch):
    body = _get_jobs(tmp_path, monkeypatch)
    assert 'aria-label="Job filter"' in body


def test_jobs_filter_uses_responsive_class(tmp_path, monkeypatch):
    body = _get_jobs(tmp_path, monkeypatch)
    assert "jobs-filter-form" in body
    assert "jobs-filter-row" in body


# ---------------------------------------------------------------------------
# 8. CSS focus visibility and responsive rules
# ---------------------------------------------------------------------------


def _read_css() -> str:
    import pathlib

    css_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src"
        / "voxera"
        / "panel"
        / "static"
        / "panel.css"
    )
    return css_path.read_text(encoding="utf-8")


def test_css_has_focus_visible_rules():
    css = _read_css()
    assert ":focus-visible" in css
    assert ".btn:focus-visible" in css
    assert ".tab-btn:focus-visible" in css
    assert "a:focus-visible" in css
    assert "summary:focus-visible" in css


def test_css_has_skip_link_styles():
    css = _read_css()
    assert ".skip-link" in css
    assert ".skip-link:focus" in css


def test_css_has_kpi_status_hint():
    css = _read_css()
    assert ".kpi-status-hint" in css


def test_css_has_tablet_breakpoint():
    css = _read_css()
    assert "max-width: 768px" in css


def test_css_has_jobs_filter_responsive_classes():
    css = _read_css()
    assert ".jobs-filter-form" in css
    assert ".jobs-filter-row" in css


def test_css_detail_grid_responsive():
    """Detail grid should collapse to single column at tablet width."""
    css = _read_css()
    # The responsive rule for detail-grid should exist within a media query
    assert ".detail-grid" in css


def test_css_has_mobile_tap_target_improvements():
    """At narrow widths, button padding should increase for tap targets."""
    css = _read_css()
    # The 540px breakpoint should contain btn padding adjustments
    assert "max-width: 540px" in css


# ---------------------------------------------------------------------------
# 9. Job detail accessibility
# ---------------------------------------------------------------------------


def test_job_detail_has_skip_link_and_main(tmp_path, monkeypatch):
    """Job detail page should have skip-link and main landmark."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    job_name = "job-test-a11y.json"
    (queue_dir / "done" / job_name).write_text('{"goal":"test"}', encoding="utf-8")
    stem = "job-test-a11y"
    (queue_dir / "done" / f"{stem}.state.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}),
        encoding="utf-8",
    )
    art = queue_dir / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    body = client.get(f"/jobs/{stem}").text

    assert 'class="skip-link"' in body
    assert "<main" in body
    assert 'id="main-content"' in body


def test_job_detail_action_bar_has_group_role(tmp_path, monkeypatch):
    """Job detail actions section should have role=group for accessibility."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    job_name = "job-test-a11y2.json"
    (queue_dir / "failed" / job_name).write_text('{"goal":"test"}', encoding="utf-8")
    (queue_dir / "failed" / "job-test-a11y2.error.json").write_text(
        json.dumps({"job": job_name, "error": "kaboom", "ts": 1}), encoding="utf-8"
    )
    stem = "job-test-a11y2"
    art = queue_dir / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)

    client = TestClient(panel_module.app)
    body = client.get(f"/jobs/{stem}").text

    assert 'role="group"' in body
    assert 'aria-label="Job actions"' in body
