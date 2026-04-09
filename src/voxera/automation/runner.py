"""Minimal automation runner for due queue-submission automations (PR2).

The runner is deliberately tiny:

1. Load automation definitions from ``<queue_root>/automations/definitions/``.
2. For each *enabled* definition whose trigger kind is supported in PR2
   (``once_at`` or ``delay``), decide whether it is due.
3. When a definition is due, emit a normal canonical queue payload via the
   existing inbox submit path (``core/inbox.add_inbox_payload``). Record a
   single history entry under ``<queue_root>/automations/history/`` and
   update the saved definition (``last_run_at_ms``, ``last_job_ref``,
   ``run_history_refs``, and the one-shot ``enabled=False`` + cleared
   ``next_run_at_ms``).
4. Unsupported trigger kinds are skipped with an explicit ``skipped``
   history outcome so an operator can tell the difference between "nothing
   was due" and "a real definition exists but PR2 refuses to act on it
   yet".

Architectural rule (do not break):

> Automation is deferred queue submission, not alternate execution.

That means the runner never executes skills, never writes into
``pending/`` / ``done/`` / ``failed/`` directly, and never invents a second
execution schema. The queue remains the execution boundary. If a future PR
wants to add cron / interval / watch-path triggers, it must layer on top of
this same emit-via-inbox path.

Fail-closed semantics:

- Malformed definition files on disk are skipped (not raised) by the
  default best-effort ``list_automation_definitions`` pass, so one bad file
  cannot hide the rest of the inventory from the runner.
- Any exception raised while emitting a queue job for a supported, due
  definition is caught, recorded as an ``error`` history entry, and the
  definition's state is *not* advanced — so a transient failure cannot
  leave a one-shot definition stuck in a "half-fired" state.
- Supported-but-not-due definitions and already-fired one-shots are
  silently left alone: no queue job, no history entry, no definition
  mutation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.inbox import add_inbox_payload
from .history import (
    AutomationRunOutcome,
    build_history_record,
    generate_run_id,
    history_record_ref,
    write_history_record,
)
from .models import AutomationDefinition
from .store import (
    AutomationStoreError,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)

# PR2 actively runs only ``once_at`` and ``delay``. Every other trigger kind
# is intentionally skipped with an explicit reason so docs + history stay
# honest about what the runner does today.
SUPPORTED_TRIGGER_KINDS: frozenset[str] = frozenset({"once_at", "delay"})

AUTOMATION_SOURCE_LANE = "automation_runner"


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class AutomationRunResult:
    """Summary of one runner decision for one automation definition.

    This is the per-definition row returned by ``run_due_automations`` and
    by the internal ``process_automation_definition`` helper. It mirrors
    the shape of the durable history record but is a transient in-memory
    dataclass — the persisted history entry is the source of audit truth.
    """

    automation_id: str
    outcome: AutomationRunOutcome
    message: str
    queue_job_ref: str | None = None
    history_ref: str | None = None
    run_id: str | None = None
    trigger_kind: str | None = None


def _compute_due_anchor_ms(definition: AutomationDefinition) -> int | None:
    """Return the epoch-ms anchor at which ``definition`` becomes due.

    ``once_at``: ``trigger_config["run_at_ms"]``.
    ``delay``:   ``created_at_ms + trigger_config["delay_ms"]``.

    Both trigger kinds anchor their due time on fields that the object
    model has already validated (strict positive ints, no bool/float), so
    this helper does not need to re-validate — it trusts the durable model
    as the source of truth.

    Returns ``None`` for any trigger kind PR2 does not support.
    """
    kind = definition.trigger_kind
    config = definition.trigger_config
    if kind == "once_at":
        run_at = config.get("run_at_ms")
        if isinstance(run_at, int) and not isinstance(run_at, bool) and run_at > 0:
            return run_at
        return None
    if kind == "delay":
        delay = config.get("delay_ms")
        if isinstance(delay, int) and not isinstance(delay, bool) and delay > 0:
            return definition.created_at_ms + delay
        return None
    return None


def evaluate_due_automation(
    definition: AutomationDefinition,
    *,
    now_ms: int | None = None,
) -> tuple[bool, str]:
    """Decide whether ``definition`` should fire at ``now_ms``.

    Returns a ``(due, reason)`` pair. ``due`` is True only for supported,
    enabled, not-already-fired definitions whose anchor has been reached.
    ``reason`` always carries an operator-legible explanation — even on the
    True path, where it describes *why* the definition is considered due.

    One-shot semantics: in PR2 both ``once_at`` and ``delay`` are one-shot.
    A definition with a non-null ``last_run_at_ms`` is considered already
    fired and will not be re-emitted on a subsequent runner pass.
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()

    if not definition.enabled:
        return (False, "definition is disabled")

    if definition.trigger_kind not in SUPPORTED_TRIGGER_KINDS:
        return (
            False,
            f"trigger_kind {definition.trigger_kind!r} is not supported by the PR2 runner",
        )

    if definition.last_run_at_ms is not None:
        return (False, "one-shot definition has already fired")

    anchor = _compute_due_anchor_ms(definition)
    if anchor is None:
        return (False, "trigger_config is missing a usable due anchor")

    if stamp < anchor:
        return (False, f"not yet due (anchor_ms={anchor}, now_ms={stamp})")

    return (True, f"due (anchor_ms={anchor}, now_ms={stamp})")


def _persist_history(
    queue_root: Path,
    *,
    automation_id: str,
    run_id: str,
    triggered_at_ms: int,
    trigger_kind: str,
    outcome: AutomationRunOutcome,
    queue_job_ref: str | None,
    message: str,
    payload_template: dict[str, Any] | None,
) -> str:
    """Build + write a history record and return its stable ref string."""
    record = build_history_record(
        automation_id=automation_id,
        run_id=run_id,
        triggered_at_ms=triggered_at_ms,
        trigger_kind=trigger_kind,
        outcome=outcome,
        queue_job_ref=queue_job_ref,
        message=message,
        payload_template=payload_template,
    )
    write_history_record(queue_root, record)
    return history_record_ref(automation_id, run_id)


def _emit_queue_job(
    queue_root: Path,
    definition: AutomationDefinition,
    *,
    run_id: str,
) -> Path:
    """Submit the definition's ``payload_template`` via the inbox path.

    The payload is the saved template verbatim (copied so the durable
    definition is never mutated by enrichment). ``add_inbox_payload``
    enriches it with a ``job_intent`` block and writes a single
    ``inbox-<job_id>.json`` file. That file is the only side effect the
    runner ever produces against the queue.

    The inbox job id is derived from the runner's ``run_id`` so two
    distinct automations firing with identical payload templates at the
    same wall-clock millisecond cannot collide on the default
    goal-hash-based inbox id. The ``run_id`` is already
    ``<epoch_ms>-<sha1[:8]>`` of ``automation_id + now_ms``, which is the
    same shape an inbox job id would take anyway.
    """
    payload = dict(definition.payload_template)
    return add_inbox_payload(
        queue_root,
        payload,
        job_id=run_id,
        source_lane=AUTOMATION_SOURCE_LANE,
    )


def process_automation_definition(
    definition: AutomationDefinition,
    queue_root: Path,
    *,
    now_ms: int | None = None,
) -> AutomationRunResult:
    """Process one definition: evaluate, emit if due, persist history.

    Returns an ``AutomationRunResult`` summarizing what happened. On
    ``submitted``, the updated definition (with refreshed ``last_run_at_ms``
    / ``last_job_ref`` / ``run_history_refs`` / ``enabled=False``) has
    already been saved back through the PR1 storage layer before this
    function returns.

    On ``skipped`` or ``error``, the stored definition is left untouched
    *except* that an ``error`` outcome may still persist a history record
    so an operator can see that the runner saw the definition and declined
    to emit.
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()
    due, reason = evaluate_due_automation(definition, now_ms=stamp)

    if not due:
        return AutomationRunResult(
            automation_id=definition.id,
            outcome="skipped",
            message=reason,
            trigger_kind=definition.trigger_kind,
        )

    run_id = generate_run_id(definition.id, now_ms=stamp)

    try:
        inbox_target = _emit_queue_job(queue_root, definition, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - fail-closed audit path
        history_ref = _persist_history(
            queue_root,
            automation_id=definition.id,
            run_id=run_id,
            triggered_at_ms=stamp,
            trigger_kind=definition.trigger_kind,
            outcome="error",
            queue_job_ref=None,
            message=f"failed to emit queue job: {exc}",
            payload_template=definition.payload_template,
        )
        return AutomationRunResult(
            automation_id=definition.id,
            outcome="error",
            message=f"failed to emit queue job: {exc}",
            history_ref=history_ref,
            run_id=run_id,
            trigger_kind=definition.trigger_kind,
        )

    queue_job_ref = inbox_target.name
    history_ref = _persist_history(
        queue_root,
        automation_id=definition.id,
        run_id=run_id,
        triggered_at_ms=stamp,
        trigger_kind=definition.trigger_kind,
        outcome="submitted",
        queue_job_ref=queue_job_ref,
        message=reason,
        payload_template=definition.payload_template,
    )

    updated = definition.model_copy(
        update={
            "enabled": False,
            "last_run_at_ms": stamp,
            "last_job_ref": queue_job_ref,
            "run_history_refs": [*definition.run_history_refs, history_ref],
            "next_run_at_ms": None,
        }
    )
    save_automation_definition(updated, queue_root, now_ms=stamp)

    return AutomationRunResult(
        automation_id=definition.id,
        outcome="submitted",
        message=reason,
        queue_job_ref=queue_job_ref,
        history_ref=history_ref,
        run_id=run_id,
        trigger_kind=definition.trigger_kind,
    )


def run_automation_once(
    automation_id: str,
    queue_root: Path,
    *,
    now_ms: int | None = None,
) -> AutomationRunResult:
    """Load one definition by id and process it through the runner.

    Raises ``AutomationStoreError`` / ``AutomationNotFoundError`` if the id
    is not legal or the file is absent/malformed. This is the narrow
    library-first entrypoint used by tests and by the minimal CLI hook.
    """
    definition = load_automation_definition(automation_id, queue_root)
    return process_automation_definition(definition, queue_root, now_ms=now_ms)


def run_due_automations(
    queue_root: Path,
    *,
    now_ms: int | None = None,
) -> list[AutomationRunResult]:
    """Walk every valid definition under ``queue_root`` and process each.

    Malformed definitions on disk are silently skipped (best-effort mode of
    ``list_automation_definitions``) so one bad file cannot hide the rest
    of the inventory. Strict mode is not used here because the runner is
    operator-facing and should keep going when possible. Malformed files
    are still visible on disk for diagnosis.

    Returns one ``AutomationRunResult`` per *valid* definition the runner
    considered, in the same sorted-by-id order as the store.
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()
    try:
        definitions = list_automation_definitions(queue_root)
    except AutomationStoreError:
        return []

    results: list[AutomationRunResult] = []
    for definition in definitions:
        results.append(process_automation_definition(definition, queue_root, now_ms=stamp))
    return results


__all__ = [
    "AUTOMATION_SOURCE_LANE",
    "AutomationRunResult",
    "SUPPORTED_TRIGGER_KINDS",
    "evaluate_due_automation",
    "process_automation_definition",
    "run_automation_once",
    "run_due_automations",
]
