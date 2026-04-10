"""Minimal automation runner for due queue-submission automations.

The runner is deliberately tiny:

1. Load automation definitions from ``<queue_root>/automations/definitions/``.
2. For each *enabled* definition whose trigger kind is supported
   (``once_at``, ``delay``, or ``recurring_interval``), decide whether it
   is due.
3. When a definition is due, emit a normal canonical queue payload via the
   existing inbox submit path (``core/inbox.add_inbox_payload``). Record a
   single history entry under ``<queue_root>/automations/history/`` and
   update the saved definition (``last_run_at_ms``, ``last_job_ref``,
   ``run_history_refs``).

   - **One-shot** (``once_at``, ``delay``): the definition is disabled
     after a successful fire (``enabled=False``, ``next_run_at_ms=None``).
   - **Recurring** (``recurring_interval``): the definition stays enabled
     and ``next_run_at_ms`` is re-armed to ``fired_at_ms + interval_ms``
     so it fires again on the next runner pass after that time.

4. Unsupported trigger kinds (``recurring_cron``, ``watch_path``) are
   skipped with an explicit ``skipped`` outcome so an operator can tell
   the difference between "nothing was due" and "a real definition exists
   but the runner refuses to act on it yet".

Architectural rule (do not break):

> Automation is deferred queue submission, not alternate execution.

That means the runner never executes skills, never writes into
``pending/`` / ``done/`` / ``failed/`` directly, and never invents a second
execution schema. The queue remains the execution boundary.

Recurring-interval semantics:

- If ``next_run_at_ms`` is already set, use it as the due anchor.
- Otherwise initialize from ``created_at_ms + interval_ms``.
- After a successful fire, set ``next_run_at_ms = fired_at_ms + interval_ms``.
- If the runner wakes up late, emit at most one queue job and schedule
  the next interval from the actual fire time (no catch-up bursts).

Fail-closed semantics:

- Malformed definition files on disk are skipped (not raised) by the
  default best-effort ``list_automation_definitions`` pass, so one bad file
  cannot hide the rest of the inventory from the runner.
- Any exception raised while emitting a queue job for a supported, due
  definition is caught, recorded as an ``error`` history entry, and the
  definition's state is *not* advanced — so a transient failure cannot
  leave a definition stuck in a "half-fired" state.
- If the queue job is emitted successfully but the follow-up save of the
  updated definition fails (e.g. disk full, permission error), a second
  ``error`` history record is written that references the successful
  ``queue_job_ref`` and describes the save failure. The runner returns
  an ``error`` result for the definition so a reviewer can see that the
  queue side of the fire succeeded but the durable definition state is
  now stale — the operator is expected to reconcile manually.
- Supported-but-not-due definitions and already-fired one-shots are
  silently left alone: no queue job, no history entry, no definition
  mutation.
- No history record is written for ordinary ``skipped`` passes. History
  is an audit trail of actual fires and errors, not a log of every
  runner pass that found nothing to do.

Concurrency note:

The runner is meant to be invoked synchronously (library call or
``voxera automation run-due-once``). The ``run_due_automations_locked``
wrapper acquires a dedicated POSIX advisory lock
(``<queue_root>/automations/.runner.lock``) before evaluating
definitions.  If the lock is already held the wrapper returns
immediately with a ``busy`` status — no definitions are loaded and no
queue jobs are submitted.  This prevents double-submit races when
multiple invocations overlap (e.g. a systemd timer firing while an
operator runs ``run-due-once`` manually).

The runner lock is distinct from the queue daemon lock
(``<queue_root>/.daemon.lock``): the daemon serializes queue execution;
the runner lock serializes automation evaluation/submission only.
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
from .lock import acquire_runner_lock, release_runner_lock
from .models import AutomationDefinition
from .store import (
    AutomationStoreError,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)

# Trigger kinds the runner actively evaluates and fires.
SUPPORTED_TRIGGER_KINDS: frozenset[str] = frozenset({"once_at", "delay", "recurring_interval"})

# One-shot trigger kinds disable the definition after a successful fire.
# Recurring trigger kinds stay enabled and re-arm ``next_run_at_ms``.
ONE_SHOT_TRIGGER_KINDS: frozenset[str] = frozenset({"once_at", "delay"})

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

    ``once_at``:            ``trigger_config["run_at_ms"]``.
    ``delay``:              ``created_at_ms + trigger_config["delay_ms"]``.
    ``recurring_interval``: ``next_run_at_ms`` if set, otherwise
                            ``created_at_ms + trigger_config["interval_ms"]``.

    All trigger kinds anchor their due time on fields that the object
    model has already validated (strict positive ints, no bool/float), so
    this helper does not need to re-validate — it trusts the durable model
    as the source of truth.

    Returns ``None`` for any trigger kind the runner does not support.
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
    if kind == "recurring_interval":
        if definition.next_run_at_ms is not None:
            return definition.next_run_at_ms
        interval = config.get("interval_ms")
        if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
            return definition.created_at_ms + interval
        return None
    return None


def evaluate_due_automation(
    definition: AutomationDefinition,
    *,
    now_ms: int | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """Decide whether ``definition`` should fire at ``now_ms``.

    Returns a ``(due, reason)`` pair. ``due`` is True only for supported,
    enabled, not-already-fired (one-shot) or due-again (recurring)
    definitions whose anchor has been reached. ``reason`` always carries
    an operator-legible explanation — even on the True path, where it
    describes *why* the definition is considered due.

    When ``force`` is True (used by ``voxera automation run-now``), the
    due-time check and the one-shot "already fired" guard are bypassed so
    the definition fires immediately. The disabled and unsupported-trigger-
    kind guards are still enforced — the operator can ``enable`` a disabled
    definition before forcing a run.

    One-shot semantics (``once_at``, ``delay``): a definition with a
    non-null ``last_run_at_ms`` is considered already fired and will not
    be re-emitted on a subsequent runner pass.

    Recurring semantics (``recurring_interval``): ``last_run_at_ms`` does
    not disqualify the definition. The due anchor is ``next_run_at_ms``
    (or ``created_at_ms + interval_ms`` on the first pass).
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()

    if not definition.enabled:
        return (False, "definition is disabled")

    if definition.trigger_kind not in SUPPORTED_TRIGGER_KINDS:
        return (
            False,
            f"trigger_kind {definition.trigger_kind!r} is not supported by the runner",
        )

    if force:
        return (True, f"forced (operator run-now, now_ms={stamp})")

    # One-shot guard: once_at and delay fire at most once.
    if definition.trigger_kind in ONE_SHOT_TRIGGER_KINDS and definition.last_run_at_ms is not None:
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
    force: bool = False,
) -> AutomationRunResult:
    """Process one definition: evaluate, emit if due, persist history.

    Returns an ``AutomationRunResult`` summarizing what happened.

    When ``force`` is True (used by ``voxera automation run-now``), the
    due-time check is bypassed so the definition fires immediately. The
    disabled and unsupported-trigger-kind guards are still enforced.

    On ``submitted``, the updated definition has already been saved back
    through the storage layer before this function returns:

    - **One-shot** (``once_at``, ``delay``): ``enabled=False``,
      ``next_run_at_ms=None``.
    - **Recurring** (``recurring_interval``): ``enabled=True``,
      ``next_run_at_ms = fired_at_ms + interval_ms``.

    In both cases ``last_run_at_ms``, ``last_job_ref``, and
    ``run_history_refs`` are updated.

    On ``skipped``, the stored definition is left untouched and no
    history record is written — history is an audit trail of actual
    fires, not of every "nothing to do" runner pass.

    On ``error``, the stored definition is left untouched and a single
    ``error`` history record is persisted describing what went wrong. If
    the queue emission succeeded but the follow-up definition save
    failed, a second ``error`` history record is persisted that carries
    the successful ``queue_job_ref`` so a reviewer can see the mixed
    state explicitly.
    """
    stamp = int(now_ms) if now_ms is not None else _now_ms()
    due, reason = evaluate_due_automation(definition, now_ms=stamp, force=force)

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
        emit_error_message = f"failed to emit queue job: {exc}"
        history_ref = _persist_history(
            queue_root,
            automation_id=definition.id,
            run_id=run_id,
            triggered_at_ms=stamp,
            trigger_kind=definition.trigger_kind,
            outcome="error",
            queue_job_ref=None,
            message=emit_error_message,
            payload_template=definition.payload_template,
        )
        return AutomationRunResult(
            automation_id=definition.id,
            outcome="error",
            message=emit_error_message,
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

    # Build the post-submit state update. One-shot kinds disable the
    # definition; recurring kinds stay enabled and re-arm next_run_at_ms.
    state_update: dict[str, Any] = {
        "last_run_at_ms": stamp,
        "last_job_ref": queue_job_ref,
        "run_history_refs": [*definition.run_history_refs, history_ref],
    }
    if definition.trigger_kind in ONE_SHOT_TRIGGER_KINDS:
        state_update["enabled"] = False
        state_update["next_run_at_ms"] = None
    elif definition.trigger_kind == "recurring_interval":
        # interval_ms is guaranteed present and positive-int by model
        # validation in _validate_trigger_config; use [] not .get().
        interval_ms: int = definition.trigger_config["interval_ms"]
        state_update["enabled"] = True
        state_update["next_run_at_ms"] = stamp + interval_ms

    updated = definition.model_copy(update=state_update)
    try:
        save_automation_definition(updated, queue_root, now_ms=stamp)
    except Exception as exc:  # noqa: BLE001 - fail-closed audit path
        # The queue job was successfully emitted and the initial submit
        # history record was written, but the follow-up save of the
        # updated definition failed. Write a second history record that
        # carries the successful queue_job_ref and the save error text
        # so a reviewer can see the mixed state explicitly. The caller
        # sees an ``error`` outcome — not a success — because the
        # definition is now durable-stale and the operator will need to
        # reconcile (either disable it manually or accept a double fire
        # on the next runner pass).
        save_error_message = (
            f"queue job {queue_job_ref} was emitted but definition state save failed: {exc}"
        )
        save_error_run_id = generate_run_id(definition.id, now_ms=stamp + 1)
        save_error_history_ref = _persist_history(
            queue_root,
            automation_id=definition.id,
            run_id=save_error_run_id,
            triggered_at_ms=stamp,
            trigger_kind=definition.trigger_kind,
            outcome="error",
            queue_job_ref=queue_job_ref,
            message=save_error_message,
            payload_template=definition.payload_template,
        )
        return AutomationRunResult(
            automation_id=definition.id,
            outcome="error",
            message=save_error_message,
            queue_job_ref=queue_job_ref,
            history_ref=save_error_history_ref,
            run_id=save_error_run_id,
            trigger_kind=definition.trigger_kind,
        )

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


@dataclass(frozen=True)
class RunnerPassResult:
    """Summary of a complete runner pass (all definitions, with lock status).

    This is the top-level result returned by ``run_due_automations_locked``.
    It wraps the per-definition results from ``run_due_automations`` together
    with the lock acquisition outcome so callers and operators can tell:

    - ``status="busy"`` — lock was held, nothing evaluated.
    - ``status="ok"``   — lock acquired, definitions evaluated.
    """

    status: str
    message: str
    results: list[AutomationRunResult]


def run_due_automations_locked(
    queue_root: Path,
    *,
    now_ms: int | None = None,
) -> RunnerPassResult:
    """Locked wrapper around ``run_due_automations``.

    Acquires the automation runner single-writer lock before evaluating
    definitions.  If the lock is already held (another runner is active),
    returns immediately with ``status="busy"`` and an empty results list —
    no definitions are loaded and no queue jobs are submitted.
    """
    lock = acquire_runner_lock(queue_root)
    if not lock.acquired:
        return RunnerPassResult(
            status="busy",
            message=lock.message,
            results=[],
        )
    try:
        results = run_due_automations(queue_root, now_ms=now_ms)
    finally:
        release_runner_lock(lock)

    submitted = sum(1 for r in results if r.outcome == "submitted")
    skipped = sum(1 for r in results if r.outcome == "skipped")
    errors = sum(1 for r in results if r.outcome == "error")
    summary_parts: list[str] = []
    if submitted:
        summary_parts.append(f"{submitted} submitted")
    if skipped:
        summary_parts.append(f"{skipped} skipped")
    if errors:
        summary_parts.append(f"{errors} errors")
    summary = ", ".join(summary_parts) if summary_parts else "no definitions found"

    return RunnerPassResult(
        status="ok",
        message=summary,
        results=results,
    )


__all__ = [
    "AUTOMATION_SOURCE_LANE",
    "AutomationRunResult",
    "ONE_SHOT_TRIGGER_KINDS",
    "RunnerPassResult",
    "SUPPORTED_TRIGGER_KINDS",
    "evaluate_due_automation",
    "process_automation_definition",
    "run_automation_once",
    "run_due_automations",
    "run_due_automations_locked",
]
