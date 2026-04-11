"""Focused tests for the read-only shared Vera session context on the
panel job-detail surface.

Scope: pins the bounded, product-facing enhancement that attaches an
optional ``vera_context`` block to the job-detail payload built by
``voxera.panel.job_detail_sections.build_job_detail_payload`` and
renders a small "Vera activity" strip in
``src/voxera/panel/templates/job_detail.html``.

Design intent covered by these tests:

* The panel reads shared session context via the existing
  ``voxera.vera.session_store.read_session_context`` API. It never
  writes / updates / clears shared context — writes are exclusively
  the responsibility of the Vera service.
* Missing session context is not an error. A job with no owning Vera
  session, an owning session with no shared context yet, or an owning
  session whose context is the canonical empty shape all produce
  ``vera_context: None`` without raising and without leaking any
  placeholder junk into the template.
* Wrong-session isolation. When multiple sessions exist and only one
  actually tracks the job via
  ``linked_queue_jobs.tracked[].job_ref``, only that session's context
  is surfaced. Context from an unrelated session must not leak.
* Staleness is computed conservatively against the state-sidecar
  ``completed_at_ms`` terminal timestamp — if either side is missing,
  ``is_stale`` is ``None`` (undecidable), not a guess.
* Queue / job-detail truth remains primary. ``vera_context`` is a
  supplemental continuity aid only.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.panel import job_detail_sections
from voxera.panel.job_detail_sections import (
    _build_vera_context,
    _find_vera_session_id_for_job,
    build_job_detail_payload,
)
from voxera.vera import session_store
from voxera.vera.session_store import (
    read_session_context,
    update_session_context,
    write_session_context,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_queue_root(tmp_path: Path) -> Path:
    queue_root = tmp_path / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_root / bucket).mkdir(parents=True, exist_ok=True)
    (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_root / "artifacts").mkdir(parents=True, exist_ok=True)
    return queue_root


def _write_done_job(queue_root: Path, job_name: str, *, completed_at_ms: int) -> None:
    (queue_root / "done" / job_name).write_text(json.dumps({"goal": "something"}), encoding="utf-8")
    stem = Path(job_name).stem
    (queue_root / "done" / f"{stem}.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "completed_at_ms": completed_at_ms,
            }
        ),
        encoding="utf-8",
    )
    art = queue_root / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
            }
        ),
        encoding="utf-8",
    )


def _write_pending_job(queue_root: Path, job_name: str) -> None:
    (queue_root / "pending" / job_name).write_text(
        json.dumps({"goal": "something"}), encoding="utf-8"
    )


def _register_session_tracking_job(queue_root: Path, session_id: str, *, job_ref: str) -> None:
    session_store.register_session_linked_job(queue_root, session_id, job_ref=job_ref)


# ---------------------------------------------------------------------------
# 1. context present -> vera_context surfaced
# ---------------------------------------------------------------------------


def test_vera_context_present_surfaces_active_topic_and_draft(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-ctx-present.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-ctx-present"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Seed shared context AFTER the job's terminal time so is_stale=False.
    write_session_context(
        queue_root,
        session_id,
        {
            "active_topic": "weather-briefing",
            "active_draft_ref": "draft://notes/weather.md",
        },
    )
    # Sanity: the read API returns a normalized, non-empty context.
    stored = read_session_context(queue_root, session_id)
    assert stored["active_topic"] == "weather-briefing"
    assert stored["active_draft_ref"] == "draft://notes/weather.md"
    assert stored["updated_at_ms"] > 0

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["session_id"] == session_id
    assert vera_context["active_topic"] == "weather-briefing"
    assert vera_context["active_draft_ref"] == "draft://notes/weather.md"
    assert isinstance(vera_context["updated_at_ms"], int)
    assert vera_context["updated_at_ms"] > 0
    # Context is newer than the job's completed_at_ms -> not stale.
    assert vera_context["is_stale"] is False


def test_vera_context_present_partial_topic_only(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-ctx-partial.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-ctx-partial"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    update_session_context(queue_root, session_id, active_topic="ops-incident")

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["active_topic"] == "ops-incident"
    # Partial context: draft ref absent, do not invent a placeholder.
    assert vera_context["active_draft_ref"] is None


# ---------------------------------------------------------------------------
# 2. context absent -> no error, no block, no leaked placeholder
# ---------------------------------------------------------------------------


def test_vera_context_absent_when_no_session_tracks_job(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-no-session.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    payload = build_job_detail_payload(queue_root, job_name)
    # vera_context is present as a top-level key but None when there is no
    # owning Vera session for the job (fail-soft, no placeholder junk).
    assert "vera_context" in payload
    assert payload["vera_context"] is None


def test_vera_context_absent_when_session_has_no_context_yet(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-empty-ctx.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-empty-ctx"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Do NOT seed any shared context. read_session_context returns the
    # canonical empty shape; vera_context must be None.

    payload = build_job_detail_payload(queue_root, job_name)
    assert payload["vera_context"] is None


def test_vera_context_absent_when_sessions_directory_missing(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    # No vera_sessions directory created at all.
    job_name = "inbox-no-sessions-dir.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    payload = build_job_detail_payload(queue_root, job_name)
    assert payload["vera_context"] is None


def test_find_vera_session_id_for_job_ignores_malformed_session_files(
    tmp_path: Path,
) -> None:
    queue_root = _make_queue_root(tmp_path)
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Malformed / garbage session file must not raise and must not match.
    (sessions_dir / "broken.json").write_text("{not json", encoding="utf-8")
    # Well-formed but unrelated session: tracks a different job.
    _register_session_tracking_job(queue_root, "vera-unrelated", job_ref="inbox-other.json")

    assert _find_vera_session_id_for_job(queue_root, "inbox-target.json") is None


# ---------------------------------------------------------------------------
# 3. context stale -> stale marker computed correctly
# ---------------------------------------------------------------------------


def test_vera_context_stale_when_context_predates_job_completion(
    tmp_path: Path,
) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-stale.json"
    # Job completes "now" — but we'll set the context's updated_at_ms
    # to a value strictly before it, simulating a context that has not
    # caught up to the job's terminal outcome.
    terminal_at_ms = 1_800_000_000_000
    _write_done_job(queue_root, job_name, completed_at_ms=terminal_at_ms)

    session_id = "vera-stale"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Seed context, then forcibly rewrite its updated_at_ms to a value
    # before terminal_at_ms via the low-level write path.
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "old-topic", "active_draft_ref": "draft://old.md"},
    )
    session_payload = session_store._read_session_payload(queue_root, session_id)
    shared = session_payload["shared_context"]
    shared["updated_at_ms"] = terminal_at_ms - 60_000  # 60s before terminal
    session_payload["shared_context"] = shared
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["is_stale"] is True
    assert vera_context["updated_at_ms"] == terminal_at_ms - 60_000


def test_vera_context_staleness_is_none_when_job_not_terminal(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-pending.json"
    _write_pending_job(queue_root, job_name)  # no state sidecar -> no completed_at_ms

    session_id = "vera-pending"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "in-flight", "active_draft_ref": None},
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    # Conservative: without a terminal timestamp, we do not overclaim.
    assert vera_context["is_stale"] is None


def test_vera_context_fresh_when_context_updated_after_terminal(
    tmp_path: Path,
) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-fresh.json"
    terminal_at_ms = 1_700_000_000_000
    _write_done_job(queue_root, job_name, completed_at_ms=terminal_at_ms)

    session_id = "vera-fresh"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Seed context and bump updated_at_ms forward past the terminal time.
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "post-run", "active_draft_ref": None},
    )
    session_payload = session_store._read_session_payload(queue_root, session_id)
    shared = session_payload["shared_context"]
    shared["updated_at_ms"] = terminal_at_ms + 5_000
    session_payload["shared_context"] = shared
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["is_stale"] is False


# ---------------------------------------------------------------------------
# 4. wrong-session isolation
# ---------------------------------------------------------------------------


def test_vera_context_does_not_leak_from_unrelated_session(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-isolated.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    # Session A actually submitted the job — its context should surface.
    session_a = "vera-owning-a"
    _register_session_tracking_job(queue_root, session_a, job_ref=job_name)
    write_session_context(
        queue_root,
        session_a,
        {"active_topic": "A-topic", "active_draft_ref": "draft://A.md"},
    )

    # Session B tracks a *different* job and has its own loud context.
    # Its context must never leak into the detail payload for job_name.
    session_b = "vera-unrelated-b"
    _register_session_tracking_job(queue_root, session_b, job_ref="inbox-some-other.json")
    write_session_context(
        queue_root,
        session_b,
        {"active_topic": "B-topic", "active_draft_ref": "draft://B.md"},
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["session_id"] == session_a
    assert vera_context["active_topic"] == "A-topic"
    assert vera_context["active_draft_ref"] == "draft://A.md"
    # Belt-and-suspenders: nothing from session B bleeds through.
    assert "B-topic" not in json.dumps(vera_context)
    assert "draft://B.md" not in json.dumps(vera_context)


def test_vera_context_returns_none_for_job_with_only_unrelated_sessions(
    tmp_path: Path,
) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-orphan.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    # Several sessions exist — none track *this* job.
    for session_id, tracked_ref in (
        ("vera-a", "inbox-not-ours-1.json"),
        ("vera-b", "inbox-not-ours-2.json"),
        ("vera-c", "inbox-not-ours-3.json"),
    ):
        _register_session_tracking_job(queue_root, session_id, job_ref=tracked_ref)
        write_session_context(
            queue_root,
            session_id,
            {"active_topic": f"{session_id}-topic", "active_draft_ref": None},
        )

    payload = build_job_detail_payload(queue_root, job_name)
    assert payload["vera_context"] is None


# ---------------------------------------------------------------------------
# 5. panel remains read-only with respect to shared context
# ---------------------------------------------------------------------------


def test_build_vera_context_does_not_mutate_shared_context(tmp_path: Path) -> None:
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-readonly.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-readonly"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "readonly-topic", "active_draft_ref": "draft://readonly.md"},
    )

    before = read_session_context(queue_root, session_id)
    before_updated_at = before["updated_at_ms"]
    # Call build_job_detail_payload (and thus _build_vera_context) multiple
    # times and confirm no fields — especially updated_at_ms — change.
    for _ in range(3):
        _ = build_job_detail_payload(queue_root, job_name)
        _ = _build_vera_context(queue_root, job_name, state_sidecar={})
    after = read_session_context(queue_root, session_id)
    assert after == before
    assert after["updated_at_ms"] == before_updated_at


def test_job_detail_sections_module_does_not_import_mutation_helpers() -> None:
    """Belt-and-suspenders: the panel builder imports only the read-only
    shared-context surface, never a write/update/clear helper.
    """
    import ast
    import inspect

    source = inspect.getsource(job_detail_sections)
    tree = ast.parse(source)
    forbidden = {
        "write_session_context",
        "update_session_context",
        "clear_session_context",
    }
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            for alias in node.names:
                imported_names.add(alias.name)
    assert "read_session_context" in imported_names
    assert not (imported_names & forbidden), (
        "panel job_detail_sections must not import shared-context mutation helpers"
    )


# ---------------------------------------------------------------------------
# 6. template renders the strip only when vera_context is present
# ---------------------------------------------------------------------------


def test_job_detail_template_renders_vera_activity_strip_when_present(
    tmp_path: Path, monkeypatch
) -> None:
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    job_name = "inbox-render-present.json"
    _write_done_job(queue_dir, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-render-present"
    _register_session_tracking_job(queue_dir, session_id, job_ref=job_name)
    write_session_context(
        queue_dir,
        session_id,
        {
            "active_topic": "render-topic",
            "active_draft_ref": "draft://render.md",
        },
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get(f"/jobs/{job_name}")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" in body
    assert "render-topic" in body
    assert "draft://render.md" in body
    # Freshness label must reflect the read-only, supplemental nature.
    assert "Read-only shared Vera session context" in body


def test_job_detail_template_hides_vera_activity_strip_when_absent(
    tmp_path: Path, monkeypatch
) -> None:
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    job_name = "inbox-render-absent.json"
    _write_done_job(queue_dir, job_name, completed_at_ms=1_700_000_000_000)
    # No session tracks this job -> vera_context is None -> strip hidden.

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get(f"/jobs/{job_name}")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" not in body
    assert "Read-only shared Vera session context" not in body
