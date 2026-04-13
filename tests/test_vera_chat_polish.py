"""Focused tests for Vera chat interface polish.

Pins the key structural and styling surfaces added/changed by the
Vera chat polish PR. Not a visual snapshot test — just verifies that
the expected CSS rules, template structures, and JS behaviours are
present so they can't be silently removed or broken by a later PR.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from voxera.vera_web import app as vera_app_module


def _set_queue_root(monkeypatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


# ─── CSS structure pins ──────────────────────────────────────────


def test_vera_css_contains_thinking_indicator_rules():
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".thinking-indicator" in css
    assert ".thinking-indicator.is-visible" in css
    assert ".thinking-indicator .thinking-role" in css
    assert ".thinking-indicator .thinking-content" in css
    assert ".thinking-indicator .dots span" in css


def test_vera_css_contains_consecutive_grouping_rules():
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".bubble.assistant + .bubble.assistant" in css
    assert ".bubble.user + .bubble.user" in css


def test_vera_css_contains_role_dot_for_assistant():
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".bubble.assistant .role::before" in css


def test_vera_css_contains_keyboard_hint():
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".composer::after" in css
    assert "Enter to send" in css
    assert ".composer:focus-within::after" in css


def test_vera_css_contains_tour_hint_styling():
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".empty-tour-hint" in css
    assert ".empty-tour-hint strong" in css


def test_vera_css_removed_unused_responding_indicator():
    """The .responding-indicator was never used in the HTML and is now
    replaced by .thinking-indicator. Ensure it's gone."""
    css_path = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".responding-indicator" not in css


# ─── Template / page structure pins ──────────────────────────────


def test_vera_page_renders_thread_and_composer(tmp_path, monkeypatch):
    """Core structural elements survive the polish changes."""
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'id="thread"' in res.text
    assert 'class="composer"' in res.text
    assert 'id="send-btn"' in res.text
    assert 'id="message-input"' in res.text


def test_vera_page_js_creates_thinking_indicator_on_submit(tmp_path, monkeypatch):
    """The JS creates a thinking-indicator element on form submit."""
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert "thinking-indicator" in res.text
    assert "is-visible" in res.text
    assert "thinking-role" in res.text
    assert "Thinking" in res.text


def test_vera_page_js_skips_poll_during_submit(tmp_path, monkeypatch):
    """The freshness poll is guarded to skip during form submission."""
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert "isSubmitting" in res.text
    assert "skip polling while form is submitting" in res.text


def test_vera_css_served_via_static(tmp_path, monkeypatch):
    """vera.css is accessible via the static mount."""
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera.css")
    assert res.status_code == 200
    assert ".thread" in res.text
    assert ".composer" in res.text
    assert ".thinking-indicator" in res.text
