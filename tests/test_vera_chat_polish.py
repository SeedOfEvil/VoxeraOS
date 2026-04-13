"""Focused tests for Vera chat interface polish.

Pins the key structural and styling surfaces added/changed by the
Vera chat polish PR.  Not a visual snapshot test — just verifies that
the expected CSS rules, template structures, and JS behaviours are
present so they can't be silently removed or broken by a later PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voxera.vera_web import app as vera_app_module

_CSS_PATH = Path(vera_app_module.__file__).resolve().parent / "static" / "vera.css"


def _set_queue_root(monkeypatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


@pytest.fixture(scope="module")
def vera_css() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


# ─── CSS structure pins ──────────────────────────────────────────


def test_vera_css_contains_thinking_indicator_rules(vera_css):
    assert ".thinking-indicator" in vera_css
    assert ".thinking-indicator.is-visible" in vera_css
    assert ".thinking-indicator .thinking-role" in vera_css
    assert ".thinking-indicator .thinking-content" in vera_css
    assert ".thinking-indicator .dots span" in vera_css


def test_vera_css_contains_consecutive_grouping_rules(vera_css):
    assert ".bubble.assistant + .bubble.assistant" in vera_css
    assert ".bubble.user + .bubble.user" in vera_css


def test_vera_css_accent_dot_is_shared_rule(vera_css):
    """The accent dot for assistant role and thinking-role is a single
    combined rule — not duplicated across two blocks."""
    assert ".bubble.assistant .role::before" in vera_css
    assert ".thinking-indicator .thinking-role::before" in vera_css
    # Both selectors appear on the same (or adjacent) line in the combined rule
    dot_idx = vera_css.index(".bubble.assistant .role::before")
    think_idx = vera_css.index(".thinking-indicator .thinking-role::before")
    # They should be within ~120 chars of each other (same rule block)
    assert abs(dot_idx - think_idx) < 120


def test_vera_css_contains_keyboard_hint(vera_css):
    assert ".composer::after" in vera_css
    assert "Enter to send" in vera_css
    assert ".composer:focus-within::after" in vera_css


def test_vera_css_contains_tour_hint_styling(vera_css):
    assert ".empty-tour-hint" in vera_css
    assert ".empty-tour-hint strong" in vera_css


def test_vera_css_removed_unused_responding_indicator(vera_css):
    """The .responding-indicator was never used in the HTML and is now
    replaced by .thinking-indicator.  Ensure it's gone."""
    assert ".responding-indicator" not in vera_css


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


def test_vera_page_js_echoes_user_message_on_submit(tmp_path, monkeypatch):
    """The JS appends a user bubble with the submitted text before the
    thinking indicator, so the user sees their message immediately."""
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    # The JS creates an article.bubble.user with textContent = msg
    assert "userBubble" in res.text
    assert "'bubble user'" in res.text
    assert "msg" in res.text  # textContent = msg (the trimmed user input)


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
