"""Home-page Vera activity helper.

Owns the small, read-only lookup that powers the home-page "Vera activity"
strip. The strip is a **supplemental continuity aid only** — canonical
panel truth (queue counts, daemon health, approvals, jobs, artifacts,
runtime health) remains primary and must never be overridden or obscured
by shared session context.

Design intent:

* The panel reads shared Vera session context via the existing
  ``voxera.vera.session_store.read_session_context`` API. It **never**
  writes / updates / clears shared context — writes are exclusively the
  responsibility of the Vera service.
* The home-page strip surfaces at most **one** session context — the
  most-recently-updated Vera session that carries a usable continuity
  signal. When multiple sessions exist, the freshest signal-bearing
  session wins.
* The "usable" gate mirrors the job-detail helper in
  ``job_detail_sections._build_vera_context``: at least one of
  ``active_topic`` / ``active_draft_ref`` / ``last_saved_file_ref`` /
  ``last_submitted_job_ref`` / ``last_completed_job_ref`` must be a
  non-empty string. A context whose only non-empty field is
  ``updated_at_ms`` carries no operator-visible signal and is treated
  as absent so the strip does not render as empty noise.
* The lookup is **fail-soft**: missing sessions directory, malformed
  session files, empty contexts, whitespace-only values, or no session
  with a usable continuity signal all produce ``None`` without raising.
* Freshness on the home page is computed against wall-clock time
  (injectable via the ``now_ms`` callable for test determinism):

  * ``fresh`` — updated within the last hour;
  * ``aging`` — updated within the last 24 hours;
  * ``stale`` — updated more than 24 hours ago;
  * ``unknown`` — no usable ``updated_at_ms`` stamp.

  The home-page strip does NOT attempt to compare the context against
  a specific job's terminal timestamp (that's the job-detail helper's
  job — it has a single job to reason about; the home page does not).

Precedence rule (enforced by the template + tests): the canonical home
sections — queue counts, daemon health widget, approvals, active jobs,
failed jobs, queue details, runtime status — remain primary. The Vera
activity strip is placed below them with a modest "supplemental only"
note and never displaces a canonical section.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..vera.session_store import read_session_context

__all__ = ["build_home_vera_activity"]


# Freshness bucket thresholds for the home-page strip.
# Operator-visible continuity aid only — NEVER used as an authority
# signal over canonical queue / health / artifact truth.
_FRESH_WINDOW_MS = 60 * 60 * 1000  # 1 hour
_AGING_WINDOW_MS = 24 * 60 * 60 * 1000  # 24 hours


def _safe_json(path: Path) -> dict[str, Any]:
    """Read ``path`` as JSON, returning ``{}`` on any failure.

    Fail-soft: missing file, unreadable file, malformed JSON, or a
    top-level non-dict payload all collapse to an empty dict so the
    home-page Vera activity lookup never raises.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _coerce_positive_int(value: Any) -> int:
    """Return ``value`` as a positive int, or 0 for anything else.

    Defensive against ``bool`` (which is a subclass of ``int`` in
    Python): treats ``True`` / ``False`` as 0 so a stray boolean in a
    session file never masquerades as a millisecond timestamp.
    Mirrors the same guard used by the job-detail helper in
    ``job_detail_sections._coerce_positive_int``.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    return 0


def _clean_ref(context: dict[str, Any], key: str) -> str | None:
    """Return ``context[key]`` as a non-empty stripped string, or ``None``."""
    raw = context.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _freshness_label(updated_at_ms: int, now_ms: int) -> str:
    """Bucket the home-page freshness label against wall-clock time.

    Returns one of ``"fresh"`` / ``"aging"`` / ``"stale"`` / ``"unknown"``.
    Conservative on missing or future timestamps:

    * ``updated_at_ms <= 0`` or ``now_ms <= 0`` → ``"unknown"``.
    * A context stamped strictly in the future (clock skew / test
      fixtures) is labeled ``"fresh"`` rather than guessed as stale.
    """
    if updated_at_ms <= 0 or now_ms <= 0:
        return "unknown"
    delta = now_ms - updated_at_ms
    if delta < 0:
        return "fresh"
    if delta <= _FRESH_WINDOW_MS:
        return "fresh"
    if delta <= _AGING_WINDOW_MS:
        return "aging"
    return "stale"


def _iter_session_ids(queue_root: Path) -> list[str]:
    """Return the list of Vera session ids under ``queue_root``.

    Fail-soft scan of ``queue_root/artifacts/vera_sessions/*.json``:
    missing directory → ``[]``; OSError during glob → ``[]``;
    malformed / non-dict session files are silently skipped. The
    returned list preserves sorted filename order (stable for tests).
    Never raises. Never writes.
    """
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return []
    try:
        session_files = sorted(sessions_dir.glob("*.json"))
    except OSError:
        return []
    session_ids: list[str] = []
    for session_file in session_files:
        if not session_file.is_file():
            continue
        payload = _safe_json(session_file)
        if not payload:
            continue
        session_id = str(payload.get("session_id") or session_file.stem).strip()
        if session_id:
            session_ids.append(session_id)
    return session_ids


def build_home_vera_activity(
    queue_root: Path,
    *,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, Any] | None:
    """Return the home-page Vera activity block, or ``None``.

    Scans ``queue_root/artifacts/vera_sessions/*.json`` read-only and
    picks the most-recently-updated session that carries a usable
    continuity signal. Returns a small dict with the signal fields
    plus ``session_id`` / ``updated_at_ms`` / ``freshness``, or
    ``None`` when no session qualifies.

    Gate (what counts as "usable"): any of ``active_topic``,
    ``active_draft_ref``, ``last_saved_file_ref``,
    ``last_submitted_job_ref``, or ``last_completed_job_ref`` must be a
    non-empty string. A context carrying only ``updated_at_ms > 0``
    with no ref signal is treated as absent so the strip does not
    render as an empty shell.

    Fail-soft: missing ``vera_sessions`` directory, unreadable
    directory, malformed session files, empty contexts, or no session
    with a usable signal all produce ``None`` without raising.

    ``now_ms`` is an injectable callable for deterministic test
    freshness; when ``None``, wall-clock ``time.time() * 1000`` is
    used. The freshness label is an operator-visible continuity hint
    only — it MUST NEVER be read as authority over canonical queue /
    health / artifact truth.
    """
    session_ids = _iter_session_ids(queue_root)
    if not session_ids:
        return None

    best: dict[str, Any] | None = None
    best_session_id: str | None = None
    best_updated_at_ms = -1

    for session_id in session_ids:
        try:
            context = read_session_context(queue_root, session_id)
        except Exception:
            continue

        active_topic = _clean_ref(context, "active_topic")
        active_draft_ref = _clean_ref(context, "active_draft_ref")
        last_saved_file_ref = _clean_ref(context, "last_saved_file_ref")
        last_submitted_job_ref = _clean_ref(context, "last_submitted_job_ref")
        last_completed_job_ref = _clean_ref(context, "last_completed_job_ref")

        if all(
            ref is None
            for ref in (
                active_topic,
                active_draft_ref,
                last_saved_file_ref,
                last_submitted_job_ref,
                last_completed_job_ref,
            )
        ):
            continue

        updated_at_ms = _coerce_positive_int(context.get("updated_at_ms"))
        # Pick the freshest signal-bearing session. Ties go to the
        # later-sorted session id, matching the sorted-filename scan
        # order for test stability.
        if updated_at_ms >= best_updated_at_ms:
            best_updated_at_ms = updated_at_ms
            best_session_id = session_id
            best = {
                "active_topic": active_topic,
                "active_draft_ref": active_draft_ref,
                "last_saved_file_ref": last_saved_file_ref,
                "last_submitted_job_ref": last_submitted_job_ref,
                "last_completed_job_ref": last_completed_job_ref,
                "updated_at_ms": updated_at_ms,
            }

    if best is None or best_session_id is None:
        return None

    now_ms_value = now_ms() if now_ms is not None else int(time.time() * 1000)
    freshness = _freshness_label(best["updated_at_ms"], now_ms_value)

    return {
        "session_id": best_session_id,
        "active_topic": best["active_topic"],
        "active_draft_ref": best["active_draft_ref"],
        "last_saved_file_ref": best["last_saved_file_ref"],
        "last_submitted_job_ref": best["last_submitted_job_ref"],
        "last_completed_job_ref": best["last_completed_job_ref"],
        "updated_at_ms": best["updated_at_ms"],
        "freshness": freshness,
    }
