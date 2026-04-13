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
from voxera.panel import home_vera_activity, job_detail_sections
from voxera.panel.home_vera_activity import build_home_vera_activity
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
    # Unpopulated continuity fields stay None — no placeholder junk.
    assert vera_context["last_saved_file_ref"] is None
    assert vera_context["last_submitted_job_ref"] is None
    assert vera_context["last_completed_job_ref"] is None
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
    # Partial context: other ref fields absent, do not invent placeholders.
    assert vera_context["active_draft_ref"] is None
    assert vera_context["last_saved_file_ref"] is None
    assert vera_context["last_submitted_job_ref"] is None
    assert vera_context["last_completed_job_ref"] is None


def test_vera_context_surfaces_when_only_last_saved_file_ref_is_set(
    tmp_path: Path,
) -> None:
    """Real-world case after submit: ``active_topic`` and ``active_draft_ref``
    are cleared when the draft is handed off, but ``last_saved_file_ref``
    remains as a continuity signal. The strip must still appear.
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-saved-only.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-saved-only"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # No topic, no draft — just the saved file ref from a prior turn.
    update_session_context(
        queue_root,
        session_id,
        last_saved_file_ref="~/VoxeraOS/notes/audit-note.txt",
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["active_topic"] is None
    assert vera_context["active_draft_ref"] is None
    assert vera_context["last_saved_file_ref"] == "~/VoxeraOS/notes/audit-note.txt"
    assert vera_context["last_submitted_job_ref"] is None


def test_vera_context_surfaces_when_only_last_submitted_job_ref_is_set(
    tmp_path: Path,
) -> None:
    """Real-world case observed in live testing: after a successful submit,
    Vera's shared context carries ``last_submitted_job_ref`` but has no
    active topic or active draft. The strip must still surface.
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-submitted-only.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-submitted-only"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    update_session_context(
        queue_root,
        session_id,
        last_submitted_job_ref="1775943976577-19a07abc",
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["active_topic"] is None
    assert vera_context["active_draft_ref"] is None
    assert vera_context["last_submitted_job_ref"] == "1775943976577-19a07abc"


def test_vera_context_surfaces_when_only_last_completed_job_ref_is_set(
    tmp_path: Path,
) -> None:
    """A session that has only observed a completed job still carries a
    useful continuity signal; the strip surfaces.
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-completed-only.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-completed-only"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    update_session_context(
        queue_root,
        session_id,
        last_completed_job_ref="inbox-prev-done.json",
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["last_completed_job_ref"] == "inbox-prev-done.json"
    assert vera_context["active_topic"] is None
    assert vera_context["active_draft_ref"] is None


def test_vera_context_surfaces_real_world_post_submit_shape(tmp_path: Path) -> None:
    """End-to-end: real-world shared_context observed in live testing for
    a Vera-submitted job (active_topic / active_draft_ref cleared;
    last_submitted_job_ref + last_saved_file_ref populated). The strip
    must render with the fallback fields and the read-only note.
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-real-world.json"
    terminal_at_ms = 1_775_943_970_000
    _write_done_job(queue_root, job_name, completed_at_ms=terminal_at_ms)

    session_id = "vera-real-world"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Stuff a shared_context that mirrors the live-testing excerpt.
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": None,
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": "1775943976577-19a07abc",
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": "~/VoxeraOS/notes/audit-note.txt",
        "ambiguity_flags": [],
        "updated_at_ms": 1_775_943_976_580,
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["active_topic"] is None
    assert vera_context["active_draft_ref"] is None
    assert vera_context["last_submitted_job_ref"] == "1775943976577-19a07abc"
    assert vera_context["last_saved_file_ref"] == "~/VoxeraOS/notes/audit-note.txt"
    # Context updated AFTER terminal -> fresh, not stale.
    assert vera_context["is_stale"] is False


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


def test_vera_context_absent_when_only_updated_at_ms_is_set(tmp_path: Path) -> None:
    """Gate: a context whose only non-empty field is ``updated_at_ms`` carries
    no operator-visible signal and must be treated as absent so the Vera
    Activity strip does not render as an empty shell.
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-ts-only.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-ts-only"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Force a context with only a timestamp — no topic, no draft ref.
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": None,
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": 1_800_000_000_000,
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    assert payload["vera_context"] is None


def test_vera_context_absent_when_topic_is_whitespace_only(tmp_path: Path) -> None:
    """A context whose topic is whitespace-only is normalized to no
    visible signal and must be treated as absent (no em-dash strip).
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-ws-only.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-ws-only"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    # Directly stuff whitespace-only values in to bypass the normalizer
    # gate, then read through the panel builder.
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": "   ",
        "active_draft_ref": "",
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": 1_800_000_000_000,
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    assert payload["vera_context"] is None


def test_vera_context_coerces_bool_updated_at_ms_to_zero(tmp_path: Path) -> None:
    """Defensive: a boolean masquerading as an int timestamp in either
    the session file or the state sidecar must never be treated as a
    positive millisecond value. ``True`` / ``False`` collapse to 0 and
    the resulting ``is_stale`` is ``None`` (undecidable).
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-bool-ts.json"
    _write_done_job(queue_root, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-bool-ts"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": "topic-with-bool-ts",
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": True,  # bool leaking into an int field
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    # Bool collapses to 0; no timestamp to compare → undecidable.
    assert vera_context["updated_at_ms"] == 0
    assert vera_context["is_stale"] is None


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


def test_vera_context_fresh_when_context_equal_to_terminal_boundary(
    tmp_path: Path,
) -> None:
    """Boundary: context updated at the exact same millisecond as the
    job's terminal completion must count as fresh (not strictly before).
    """
    queue_root = _make_queue_root(tmp_path)
    job_name = "inbox-boundary.json"
    terminal_at_ms = 1_750_000_000_000
    _write_done_job(queue_root, job_name, completed_at_ms=terminal_at_ms)

    session_id = "vera-boundary"
    _register_session_tracking_job(queue_root, session_id, job_ref=job_name)
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "edge", "active_draft_ref": None},
    )
    session_payload = session_store._read_session_payload(queue_root, session_id)
    shared = session_payload["shared_context"]
    shared["updated_at_ms"] = terminal_at_ms
    session_payload["shared_context"] = shared
    session_store._write_session_payload(queue_root, session_id, session_payload)

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["is_stale"] is False
    assert vera_context["updated_at_ms"] == terminal_at_ms


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

    # Session B tracks a *different* job and has its own loud context
    # populated across every continuity field — none of these may bleed
    # into the detail payload for job_name.
    session_b = "vera-unrelated-b"
    _register_session_tracking_job(queue_root, session_b, job_ref="inbox-some-other.json")
    write_session_context(
        queue_root,
        session_b,
        {
            "active_topic": "B-topic",
            "active_draft_ref": "draft://B.md",
            "last_saved_file_ref": "~/B-saved.txt",
            "last_submitted_job_ref": "B-submitted.json",
            "last_completed_job_ref": "B-completed.json",
        },
    )

    payload = build_job_detail_payload(queue_root, job_name)
    vera_context = payload["vera_context"]
    assert vera_context is not None
    assert vera_context["session_id"] == session_a
    assert vera_context["active_topic"] == "A-topic"
    assert vera_context["active_draft_ref"] == "draft://A.md"
    assert vera_context["last_saved_file_ref"] is None
    assert vera_context["last_submitted_job_ref"] is None
    assert vera_context["last_completed_job_ref"] is None
    # Belt-and-suspenders: nothing from session B bleeds through.
    rendered = json.dumps(vera_context)
    for b_signal in (
        "B-topic",
        "draft://B.md",
        "~/B-saved.txt",
        "B-submitted.json",
        "B-completed.json",
    ):
        assert b_signal not in rendered


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


def test_job_detail_template_renders_fallback_fields_without_topic_or_draft(
    tmp_path: Path, monkeypatch
) -> None:
    """Real-world post-submit shape: the strip must render with only
    ``last_submitted_job_ref`` / ``last_saved_file_ref`` populated, show
    those rows, and NOT render em-dash placeholder rows for the absent
    active topic / active draft fields.
    """
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    job_name = "inbox-render-fallback.json"
    _write_done_job(queue_dir, job_name, completed_at_ms=1_700_000_000_000)

    session_id = "vera-render-fallback"
    _register_session_tracking_job(queue_dir, session_id, job_ref=job_name)
    update_session_context(
        queue_dir,
        session_id,
        last_submitted_job_ref="1775943976577-19a07abc",
        last_saved_file_ref="~/VoxeraOS/notes/audit-note.txt",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get(f"/jobs/{job_name}")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" in body
    assert "Read-only shared Vera session context" in body
    # Fallback rows render.
    assert "Last submitted job" in body
    assert "1775943976577-19a07abc" in body
    assert "Last saved file" in body
    assert "~/VoxeraOS/notes/audit-note.txt" in body
    # Absent fields must NOT render as placeholder rows.
    assert "Active topic" not in body
    assert "Active draft" not in body


# ---------------------------------------------------------------------------
# 7. home-page Vera activity strip — build_home_vera_activity helper
#
# Precedence rule pinned here: the home-page strip is a supplemental
# continuity aid ONLY. Canonical panel truth (queue counts, daemon
# health, approvals, jobs, artifacts, runtime health) must always
# remain primary. These tests exercise the helper in isolation;
# template-level precedence is pinned by the end-to-end home render
# tests further down.
# ---------------------------------------------------------------------------


def _make_home_queue_root(tmp_path: Path) -> Path:
    """Build a minimal home-page queue root fixture.

    Matches the directory shape the home route expects
    (``<queue_root>/inbox|pending|done|failed|canceled``) plus the
    ``artifacts/vera_sessions`` directory that the Vera activity
    helper scans.
    """
    queue_root = tmp_path / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_root / bucket).mkdir(parents=True, exist_ok=True)
    (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_root / "artifacts").mkdir(parents=True, exist_ok=True)
    return queue_root


def test_build_home_vera_activity_returns_none_when_no_sessions_dir(
    tmp_path: Path,
) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    # No vera_sessions directory under artifacts.
    assert build_home_vera_activity(queue_root) is None


def test_build_home_vera_activity_returns_none_when_sessions_dir_empty(
    tmp_path: Path,
) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    (queue_root / "artifacts" / "vera_sessions").mkdir(parents=True, exist_ok=True)
    assert build_home_vera_activity(queue_root) is None


def test_build_home_vera_activity_returns_none_when_all_contexts_empty(
    tmp_path: Path,
) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    # Session exists but has no shared context at all.
    session_store.register_session_linked_job(queue_root, "vera-no-ctx", job_ref="inbox-x.json")
    assert build_home_vera_activity(queue_root) is None


def test_build_home_vera_activity_returns_none_when_only_updated_at_ms_set(
    tmp_path: Path,
) -> None:
    """Gate: a context whose only non-empty field is ``updated_at_ms``
    carries no operator-visible signal and must NOT drive the strip.
    """
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-ts-only"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": None,
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": 1_800_000_000_000,
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    assert build_home_vera_activity(queue_root) is None


def test_build_home_vera_activity_surfaces_single_session_with_topic(
    tmp_path: Path,
) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-single"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "ops-incident", "active_draft_ref": "draft://plan.md"},
    )

    # Use a fixed now_ms so freshness stays deterministic.
    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert activity["session_id"] == session_id
    assert activity["active_topic"] == "ops-incident"
    assert activity["active_draft_ref"] == "draft://plan.md"
    # Unpopulated continuity fields stay None — no placeholder junk.
    assert activity["last_saved_file_ref"] is None
    assert activity["last_submitted_job_ref"] is None
    assert activity["last_completed_job_ref"] is None
    assert isinstance(activity["updated_at_ms"], int)
    assert activity["updated_at_ms"] > 0
    # Context updated far earlier than now_ms → stale.
    assert activity["freshness"] == "stale"


def test_build_home_vera_activity_surfaces_single_session_fallback_fields(
    tmp_path: Path,
) -> None:
    """Real-world post-submit shape: only ``last_submitted_job_ref`` /
    ``last_saved_file_ref`` populated — the helper must still surface.
    """
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-post-submit"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    update_session_context(
        queue_root,
        session_id,
        last_submitted_job_ref="1775943976577-19a07abc",
        last_saved_file_ref="~/VoxeraOS/notes/audit-note.txt",
    )

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert activity["last_submitted_job_ref"] == "1775943976577-19a07abc"
    assert activity["last_saved_file_ref"] == "~/VoxeraOS/notes/audit-note.txt"
    assert activity["active_topic"] is None
    assert activity["active_draft_ref"] is None
    assert activity["last_completed_job_ref"] is None


def test_build_home_vera_activity_picks_freshest_session(tmp_path: Path) -> None:
    """When multiple sessions exist, pick the freshest signal-bearing one."""
    queue_root = _make_home_queue_root(tmp_path)
    # Older session.
    older_id = "vera-older"
    session_store.register_session_linked_job(queue_root, older_id, job_ref="inbox-a.json")
    write_session_context(queue_root, older_id, {"active_topic": "older-topic"})
    older_payload = session_store._read_session_payload(queue_root, older_id)
    older_payload["shared_context"]["updated_at_ms"] = 1_700_000_000_000
    session_store._write_session_payload(queue_root, older_id, older_payload)

    # Newer session.
    newer_id = "vera-newer"
    session_store.register_session_linked_job(queue_root, newer_id, job_ref="inbox-b.json")
    write_session_context(queue_root, newer_id, {"active_topic": "newer-topic"})
    newer_payload = session_store._read_session_payload(queue_root, newer_id)
    newer_payload["shared_context"]["updated_at_ms"] = 1_800_000_000_000
    session_store._write_session_payload(queue_root, newer_id, newer_payload)

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 1_800_000_500_000)
    assert activity is not None
    assert activity["session_id"] == newer_id
    assert activity["active_topic"] == "newer-topic"


def test_build_home_vera_activity_ignores_malformed_session_files(
    tmp_path: Path,
) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Garbage file — must not raise.
    (sessions_dir / "broken.json").write_text("{not json", encoding="utf-8")
    # Valid session with a real topic.
    session_id = "vera-valid"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    write_session_context(queue_root, session_id, {"active_topic": "valid-topic"})

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert activity["session_id"] == session_id
    assert activity["active_topic"] == "valid-topic"


def test_build_home_vera_activity_freshness_buckets(tmp_path: Path) -> None:
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-fresh-buckets"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    write_session_context(queue_root, session_id, {"active_topic": "t"})
    payload = session_store._read_session_payload(queue_root, session_id)
    payload["shared_context"]["updated_at_ms"] = 2_000_000_000_000
    session_store._write_session_payload(queue_root, session_id, payload)

    # 5 minutes later → fresh.
    activity = build_home_vera_activity(
        queue_root, now_ms=lambda: 2_000_000_000_000 + 5 * 60 * 1000
    )
    assert activity is not None
    assert activity["freshness"] == "fresh"

    # 3 hours later → aging.
    activity = build_home_vera_activity(
        queue_root, now_ms=lambda: 2_000_000_000_000 + 3 * 60 * 60 * 1000
    )
    assert activity is not None
    assert activity["freshness"] == "aging"

    # 30 hours later → stale.
    activity = build_home_vera_activity(
        queue_root, now_ms=lambda: 2_000_000_000_000 + 30 * 60 * 60 * 1000
    )
    assert activity is not None
    assert activity["freshness"] == "stale"


def test_build_home_vera_activity_freshness_unknown_when_no_timestamp(
    tmp_path: Path,
) -> None:
    """A context with a ref signal but a zero/missing ``updated_at_ms``
    must label freshness as ``unknown`` rather than guessing.
    """
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-no-ts"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": "topic-without-ts",
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": 0,
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert activity["active_topic"] == "topic-without-ts"
    assert activity["updated_at_ms"] == 0
    assert activity["freshness"] == "unknown"


def test_build_home_vera_activity_coerces_bool_updated_at_ms_to_zero(
    tmp_path: Path,
) -> None:
    """Defensive: ``True`` masquerading as ``updated_at_ms`` collapses
    to 0 and freshness → ``unknown``. Mirrors the job-detail helper.
    """
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-bool-ts"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    session_payload = session_store._read_session_payload(queue_root, session_id)
    session_payload["shared_context"] = {
        "active_topic": "topic-with-bool-ts",
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": True,  # bool leaking into an int field
    }
    session_store._write_session_payload(queue_root, session_id, session_payload)

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert activity["updated_at_ms"] == 0
    assert activity["freshness"] == "unknown"


def test_build_home_vera_activity_is_read_only(tmp_path: Path) -> None:
    """Panel remains strictly read-only with respect to shared session
    context. Repeated ``build_home_vera_activity`` calls must leave the
    stored context byte-for-byte unchanged.
    """
    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-readonly-home"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    write_session_context(
        queue_root,
        session_id,
        {"active_topic": "ro-topic", "active_draft_ref": "draft://ro.md"},
    )

    before = read_session_context(queue_root, session_id)
    before_updated_at = before["updated_at_ms"]
    for _ in range(3):
        _ = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    after = read_session_context(queue_root, session_id)
    assert after == before
    assert after["updated_at_ms"] == before_updated_at


def test_build_home_vera_activity_return_shape_lock(tmp_path: Path) -> None:
    """Shape lock: pin the exact top-level keys returned by
    ``build_home_vera_activity`` so a later change that silently adds,
    renames, or removes a key must update this test in the same commit.
    Mirrors the ``_EXPECTED_JOB_DETAIL_KEYS`` pattern from
    ``test_panel_job_detail_shaping_extraction.py``.
    """
    _EXPECTED_KEYS = frozenset(
        {
            "session_id",
            "active_topic",
            "active_draft_ref",
            "last_saved_file_ref",
            "last_submitted_job_ref",
            "last_completed_job_ref",
            "updated_at_ms",
            "freshness",
        }
    )

    queue_root = _make_home_queue_root(tmp_path)
    session_id = "vera-shape-lock"
    session_store.register_session_linked_job(queue_root, session_id, job_ref="inbox-x.json")
    write_session_context(queue_root, session_id, {"active_topic": "shape-test"})

    activity = build_home_vera_activity(queue_root, now_ms=lambda: 2_000_000_000_000)
    assert activity is not None
    assert set(activity.keys()) == _EXPECTED_KEYS, (
        f"build_home_vera_activity return keys changed: "
        f"added={set(activity.keys()) - _EXPECTED_KEYS}, "
        f"removed={_EXPECTED_KEYS - set(activity.keys())}"
    )


def test_home_vera_activity_module_does_not_import_mutation_helpers() -> None:
    """Belt-and-suspenders: the home-page helper imports only the
    read-only shared-context surface, never a write/update/clear helper.
    """
    import ast
    import inspect

    source = inspect.getsource(home_vera_activity)
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
        "panel home_vera_activity must not import shared-context mutation helpers"
    )


# ---------------------------------------------------------------------------
# 8. home-page end-to-end render tests — precedence, absence, and presence
# ---------------------------------------------------------------------------


def _home_fixture(tmp_path: Path) -> Path:
    """Build a home-page fixture matching the existing ``test_panel.py``
    shape. Returns the ``fake_home`` directory that the test must
    monkeypatch onto ``panel_module.Path.home``.
    """
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_dir / bucket).mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    return fake_home


def _home_queue_dir(fake_home: Path) -> Path:
    return fake_home / "VoxeraOS" / "notes" / "queue"


def test_home_renders_vera_activity_strip_when_context_present(tmp_path: Path, monkeypatch) -> None:
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    session_id = "vera-home-present"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    write_session_context(
        queue_dir,
        session_id,
        {
            "active_topic": "home-topic",
            "active_draft_ref": "draft://home.md",
        },
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    # Canonical home sections must remain primary above the strip.
    assert "Queue Summary" in body
    assert "Approval Command Center" in body
    assert "Active Work" in body
    # Vera activity strip is surfaced.
    assert "Vera Activity" in body
    assert "Read-only shared Vera session context" in body
    assert "home-topic" in body
    assert "draft://home.md" in body


def test_home_hides_vera_activity_strip_when_no_context(tmp_path: Path, monkeypatch) -> None:
    fake_home = _home_fixture(tmp_path)
    # No sessions / no shared context at all.

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    # Canonical home sections still render.
    assert "Queue Summary" in body
    assert "Approval Command Center" in body
    # Strip is hidden — no empty shell, no note leak.
    assert "Vera Activity" not in body
    assert "Read-only shared Vera session context" not in body


def test_home_hides_vera_activity_strip_when_only_updated_at_ms_set(
    tmp_path: Path, monkeypatch
) -> None:
    """Gate: a context carrying only ``updated_at_ms`` must not render
    an empty strip on the home page.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    session_id = "vera-home-ts-only"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    session_payload = session_store._read_session_payload(queue_dir, session_id)
    session_payload["shared_context"] = {
        "active_topic": None,
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "ambiguity_flags": [],
        "updated_at_ms": 1_800_000_000_000,
    }
    session_store._write_session_payload(queue_dir, session_id, session_payload)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" not in body


def test_home_renders_only_present_fields_in_vera_activity_strip(
    tmp_path: Path, monkeypatch
) -> None:
    """Real-world post-submit shape: only ``last_submitted_job_ref`` /
    ``last_saved_file_ref`` populated. The strip must render those rows
    and NOT render em-dash placeholder rows for the absent fields.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    session_id = "vera-home-fallback"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    update_session_context(
        queue_dir,
        session_id,
        last_submitted_job_ref="1775943976577-19a07abc",
        last_saved_file_ref="~/VoxeraOS/notes/audit-note.txt",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" in body
    # Fallback rows render.
    assert "Last submitted job" in body
    assert "1775943976577-19a07abc" in body
    assert "Last saved file" in body
    assert "~/VoxeraOS/notes/audit-note.txt" in body
    # Absent fields must NOT render as placeholder rows.
    assert "Active topic" not in body
    assert "Active draft" not in body
    assert "Last completed job" not in body


def test_home_vera_activity_does_not_override_canonical_queue_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """Precedence rule: even when Vera context references a
    ``last_submitted_job_ref`` that doesn't exist in the queue, the
    canonical queue counts shown on the home page remain empty — the
    Vera activity strip must never be rendered as authoritative state
    and must never inflate the queue summary cards.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    # Queue is empty — no inbox/pending/failed/done jobs at all.
    session_id = "vera-home-stale-ref"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-ghost.json")
    # Context claims a submitted job that isn't in the queue.
    update_session_context(
        queue_dir,
        session_id,
        last_submitted_job_ref="ghost-job-that-does-not-exist.json",
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    # Canonical queue truth wins: the KPI cards still show empty.
    # "No pending queue approvals" comes from the empty Approvals block;
    # "No active work" comes from the empty active work block.
    assert "No pending queue approvals" in body
    assert "No active work" in body
    # The strip surfaces the supplemental reference, but clearly labeled.
    assert "Vera Activity" in body
    assert "ghost-job-that-does-not-exist.json" in body
    assert "Supplemental only" in body


def test_home_vera_activity_does_not_override_daemon_health_widget(
    tmp_path: Path, monkeypatch
) -> None:
    """Precedence rule: even a loud Vera context must not obscure or
    replace the canonical Daemon Health widget. Both must render, with
    daemon health above the strip in the home layout.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)
    # Seed the canonical health snapshot first.
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
    # Then seed a loud Vera session context.
    session_id = "vera-home-loud"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    write_session_context(
        queue_dir,
        session_id,
        {
            "active_topic": "loud-topic",
            "active_draft_ref": "draft://loud.md",
            "last_saved_file_ref": "~/VoxeraOS/notes/loud.txt",
            "last_submitted_job_ref": "loud-submitted.json",
            "last_completed_job_ref": "loud-completed.json",
        },
    )

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    # Canonical daemon-health widget is still primary.
    assert "Daemon Health" in body
    assert "daemon_tick @ 123" in body
    # Canonical queue-truth sections still render.
    assert "Queue Summary" in body
    assert "Queue Status" in body
    assert "Approval Command Center" in body
    # Vera activity strip is surfaced as supplemental.
    assert "Vera Activity" in body
    assert "loud-topic" in body
    assert "Supplemental only" in body
    # Belt-and-suspenders: the strip is placed AFTER the daemon health
    # widget in the rendered HTML (canonical truth is visually primary).
    assert body.index("Daemon Health") < body.index("Vera Activity")
    assert body.index("Queue Summary") < body.index("Vera Activity")
    assert body.index("Approval Command Center") < body.index("Vera Activity")


def test_home_renders_normally_when_sessions_dir_missing(tmp_path: Path, monkeypatch) -> None:
    """Fail-soft: if the entire ``vera_sessions`` directory is missing,
    the home page renders normally without the strip and without any
    error.
    """
    fake_home = _home_fixture(tmp_path)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "Voxera Control Pane" in body
    assert "Queue Summary" in body
    assert "Vera Activity" not in body


def test_home_renders_normally_when_session_files_malformed(tmp_path: Path, monkeypatch) -> None:
    """Fail-soft: a garbage session file must not break the home page."""
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)
    sessions_dir = queue_dir / "artifacts" / "vera_sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "corrupt.json").write_text("{not json", encoding="utf-8")

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "Queue Summary" in body
    assert "Vera Activity" not in body


def test_home_vera_activity_strip_labels_stale_conservatively(tmp_path: Path, monkeypatch) -> None:
    """Stale context is labeled with a ``stale`` badge rather than
    being hidden outright — the operator still sees the signal but it
    is clearly marked as not-recent supplemental state.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    session_id = "vera-home-stale"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    write_session_context(queue_dir, session_id, {"active_topic": "old-topic"})
    # Force the context's timestamp well into the past so the helper
    # buckets it as ``stale`` against wall-clock now.
    session_payload = session_store._read_session_payload(queue_dir, session_id)
    session_payload["shared_context"]["updated_at_ms"] = 1_000_000_000_000  # 2001
    session_store._write_session_payload(queue_dir, session_id, session_payload)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert "Vera Activity" in body
    assert "old-topic" in body
    assert "stale" in body
    assert "Supplemental only" in body


def test_home_remains_read_only_wrt_shared_context(tmp_path: Path, monkeypatch) -> None:
    """Panel stays strictly read-only with respect to shared Vera
    session context. Repeated home-page renders must leave the stored
    context byte-for-byte unchanged.
    """
    fake_home = _home_fixture(tmp_path)
    queue_dir = _home_queue_dir(fake_home)

    session_id = "vera-home-readonly"
    session_store.register_session_linked_job(queue_dir, session_id, job_ref="inbox-x.json")
    write_session_context(
        queue_dir,
        session_id,
        {"active_topic": "ro-topic", "active_draft_ref": "draft://ro.md"},
    )
    before = read_session_context(queue_dir, session_id)

    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    client = TestClient(panel_module.app)
    for _ in range(3):
        res = client.get("/")
        assert res.status_code == 200

    after = read_session_context(queue_dir, session_id)
    assert after == before
    assert after["updated_at_ms"] == before["updated_at_ms"]


def test_home_route_does_not_import_shared_context_mutation_helpers() -> None:
    """Belt-and-suspenders: the ``routes_home`` module must only reach
    the read-only Vera activity helper — never import write/update/
    clear helpers from ``session_store``.
    """
    import ast
    import inspect

    from voxera.panel import routes_home

    source = inspect.getsource(routes_home)
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
    assert "build_home_vera_activity" in imported_names
    assert not (imported_names & forbidden), (
        "panel routes_home must not import shared-context mutation helpers"
    )
