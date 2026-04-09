"""Durable run-history records for the automation runner.

The automation runner (``src/voxera/automation/runner.py``) writes one JSON
file per run *event* into ``<queue_root>/automations/history/``. Each record
is a boring, self-describing audit row that explains what the runner decided
for a given automation at a given wall-clock time. History records are
write-once and never mutated in place.

The outcome of a single run is one of:

- ``submitted`` — the runner emitted a normal canonical queue job via the
  existing inbox submit path. ``queue_job_ref`` carries the inbox filename.
- ``skipped``   — the runner evaluated the definition but chose not to emit
  a job (not due, disabled, unsupported trigger kind, one-shot already
  fired, etc.). ``message`` carries a short reason.
- ``error``     — the runner encountered a problem processing the definition
  (e.g. malformed on disk). ``message`` carries the error text. No queue job
  is emitted in this case — fail closed.

PR2 scope note: only ``once_at`` and ``delay`` triggers are actually run.
Every other trigger kind surfaces here as a ``skipped`` record with a
reason that names the unsupported kind, so an operator tailing the history
directory can tell the difference between "nothing was due" and "a real
definition exists but PR2 refuses to act on it yet".
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Final, Literal

from .store import HISTORY_DIRNAME, automations_root, history_dir

AUTOMATION_HISTORY_SCHEMA_VERSION: Final[int] = 1

AutomationRunOutcome = Literal["submitted", "skipped", "error"]

AUTOMATION_RUN_OUTCOMES: Final[frozenset[str]] = frozenset({"submitted", "skipped", "error"})

# Same shape as ``AUTOMATION_ID_PATTERN`` in ``models.py`` but also accepts
# ``_`` as a leading char because run ids are generated from an epoch-ms
# stamp plus a short hash digest. We still reject path separators, dots, and
# anything that would let a caller-supplied id escape the history directory.
_RUN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


def generate_run_id(automation_id: str, *, now_ms: int | None = None) -> str:
    """Return a deterministic-looking, collision-resistant run id.

    Shape: ``<epoch_ms>-<sha1[:8]>``. This mirrors the style of
    ``core/inbox.py::generate_inbox_id`` so automation history ids feel
    consistent with queue job ids.
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()
    digest = hashlib.sha1(f"{automation_id}|{stamp}".encode()).hexdigest()[:8]
    return f"{stamp}-{digest}"


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, human-readable summary of a queue payload.

    The full payload is intentionally *not* copied into the history record:
    history is an audit row, not a second source of queue truth. The summary
    carries just enough for an operator to tell one run from another.
    """
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("mission_id", "goal", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            summary[key] = value.strip()
    steps = payload.get("steps")
    if isinstance(steps, list):
        summary["steps_count"] = len(steps)
    if isinstance(payload.get("write_file"), dict):
        summary["has_write_file"] = True
    if isinstance(payload.get("file_organize"), dict):
        summary["has_file_organize"] = True
    return summary


def _hash_payload(payload: dict[str, Any]) -> str:
    """Return a stable sha256 digest of the saved payload template.

    Uses sorted-key JSON so the hash is deterministic across runs and does
    not drift with dict-insertion order. This is what lets a reviewer prove
    two runs emitted the same payload shape without diffing the inbox files
    by hand.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_history_id(run_id: str) -> str:
    if not isinstance(run_id, str):
        raise ValueError("run_id must be a string")
    stripped = run_id.strip()
    if not stripped or not _RUN_ID_PATTERN.match(stripped):
        raise ValueError(f"invalid automation run_id: {run_id!r}")
    return stripped


def _history_record_name(automation_id: str, run_id: str) -> str:
    """Return the on-disk filename for a history record.

    Format: ``auto-<automation_id>-<run_id>.json``. Both segments have
    already been validated against their respective id patterns before this
    is called, so the final filename is guaranteed filesystem-safe.
    """
    return f"auto-{automation_id}-{run_id}.json"


def build_history_record(
    *,
    automation_id: str,
    run_id: str,
    triggered_at_ms: int,
    trigger_kind: str,
    outcome: AutomationRunOutcome,
    queue_job_ref: str | None,
    message: str,
    payload_template: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a fully populated automation history record dict.

    The record is JSON-serializable and stable. It intentionally omits the
    raw payload — history is an audit row, not a second copy of the queue
    truth. The ``payload_summary`` and ``payload_hash`` fields are enough
    to link a history entry back to the exact payload template that was
    emitted.
    """
    if outcome not in AUTOMATION_RUN_OUTCOMES:
        raise ValueError(f"invalid automation run outcome: {outcome!r}")
    record: dict[str, Any] = {
        "schema_version": AUTOMATION_HISTORY_SCHEMA_VERSION,
        "automation_id": automation_id,
        "run_id": run_id,
        "triggered_at_ms": int(triggered_at_ms),
        "trigger_kind": str(trigger_kind),
        "outcome": outcome,
        "queue_job_ref": queue_job_ref,
        "message": message,
    }
    if isinstance(payload_template, dict):
        record["payload_summary"] = _summarize_payload(payload_template)
        record["payload_hash"] = _hash_payload(payload_template)
    else:
        record["payload_summary"] = {}
        record["payload_hash"] = None
    return record


def write_history_record(
    queue_root: Path,
    record: dict[str, Any],
) -> Path:
    """Persist a history record under ``<queue_root>/automations/history/``.

    The write is atomic: we serialize to a ``.tmp`` sibling and then
    ``Path.replace`` onto the final target, so an interrupted writer cannot
    leave a half-written JSON file. One JSON file per run event, no in-place
    mutation, no merging.
    """
    if not isinstance(record, dict):  # pragma: no cover - guarded upstream
        raise ValueError("record must be a dict")
    automation_id = record.get("automation_id")
    run_id = record.get("run_id")
    if not isinstance(automation_id, str) or not automation_id.strip():
        raise ValueError("history record requires a non-empty automation_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("history record requires a non-empty run_id")
    validated_run_id = _validated_history_id(run_id)

    # Ensure the automations/history/ directory exists. We do not call
    # ``ensure_automation_dirs`` here to keep history writes cheap and
    # side-effect-free beyond their own subtree.
    automations_root(queue_root).mkdir(parents=True, exist_ok=True)
    target_dir = history_dir(queue_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = _history_record_name(automation_id.strip(), validated_run_id)
    target = target_dir / filename
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def history_record_ref(automation_id: str, run_id: str) -> str:
    """Return a short stable reference string for a history record.

    This is the value stored in ``AutomationDefinition.run_history_refs`` so
    a reviewer can follow the link from a definition back to its history
    entries without having to reconstruct the filename. Shape:
    ``<HISTORY_DIRNAME>/auto-<automation_id>-<run_id>.json``.
    """
    return f"{HISTORY_DIRNAME}/{_history_record_name(automation_id, run_id)}"


__all__ = [
    "AUTOMATION_HISTORY_SCHEMA_VERSION",
    "AUTOMATION_RUN_OUTCOMES",
    "AutomationRunOutcome",
    "build_history_record",
    "generate_run_id",
    "history_record_ref",
    "write_history_record",
]
