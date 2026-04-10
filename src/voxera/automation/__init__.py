"""Automation object model, storage, and minimal runner.

This package hosts the durable automation definition model, its
file-backed storage layer, and a minimal runner that consumes saved
definitions and emits normal canonical queue jobs when they become due.

The runner is deliberately scoped:

- ``once_at`` and ``delay`` trigger kinds are one-shot: they fire once
  and then disable the definition.
- ``recurring_interval`` stays enabled after each fire and re-arms
  ``next_run_at_ms`` so it fires again on the next interval.
- The runner never executes skills directly. It submits via the existing
  canonical inbox path (``core/inbox.add_inbox_payload``) so the queue
  remains the execution boundary.
- Unsupported trigger kinds (``recurring_cron``, ``watch_path``) are
  persisted but skipped by the runner.

See ``docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`` for the queue payload
shape that ``AutomationDefinition.payload_template`` must conform to.
"""

from __future__ import annotations

from .history import (
    AUTOMATION_HISTORY_SCHEMA_VERSION,
    AUTOMATION_RUN_OUTCOMES,
    AutomationRunOutcome,
    build_history_record,
    generate_run_id,
    history_record_ref,
    list_history_records,
    write_history_record,
)
from .models import (
    AUTOMATION_CANONICAL_REQUEST_FIELDS,
    AUTOMATION_CREATED_FROM_VALUES,
    AUTOMATION_ID_PATTERN,
    AUTOMATION_POLICY_POSTURES,
    AUTOMATION_TRIGGER_KINDS,
    WATCH_PATH_ALLOWED_EVENTS,
    AutomationCreatedFrom,
    AutomationDefinition,
    AutomationPolicyPosture,
    AutomationTriggerKind,
)
from .runner import (
    AUTOMATION_SOURCE_LANE,
    ONE_SHOT_TRIGGER_KINDS,
    SUPPORTED_TRIGGER_KINDS,
    AutomationRunResult,
    evaluate_due_automation,
    process_automation_definition,
    run_automation_once,
    run_due_automations,
)
from .store import (
    AUTOMATIONS_DIRNAME,
    DEFINITIONS_DIRNAME,
    HISTORY_DIRNAME,
    AutomationNotFoundError,
    AutomationStoreError,
    automations_root,
    definition_path,
    definitions_dir,
    delete_automation_definition,
    ensure_automation_dirs,
    history_dir,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)

__all__ = [
    "AUTOMATIONS_DIRNAME",
    "AUTOMATION_CANONICAL_REQUEST_FIELDS",
    "AUTOMATION_CREATED_FROM_VALUES",
    "AUTOMATION_HISTORY_SCHEMA_VERSION",
    "AUTOMATION_ID_PATTERN",
    "AUTOMATION_POLICY_POSTURES",
    "AUTOMATION_RUN_OUTCOMES",
    "AUTOMATION_SOURCE_LANE",
    "AUTOMATION_TRIGGER_KINDS",
    "DEFINITIONS_DIRNAME",
    "HISTORY_DIRNAME",
    "ONE_SHOT_TRIGGER_KINDS",
    "SUPPORTED_TRIGGER_KINDS",
    "WATCH_PATH_ALLOWED_EVENTS",
    "AutomationCreatedFrom",
    "AutomationDefinition",
    "AutomationNotFoundError",
    "AutomationPolicyPosture",
    "AutomationRunOutcome",
    "AutomationRunResult",
    "AutomationStoreError",
    "AutomationTriggerKind",
    "automations_root",
    "build_history_record",
    "definition_path",
    "definitions_dir",
    "delete_automation_definition",
    "ensure_automation_dirs",
    "evaluate_due_automation",
    "generate_run_id",
    "history_dir",
    "history_record_ref",
    "list_history_records",
    "list_automation_definitions",
    "load_automation_definition",
    "process_automation_definition",
    "run_automation_once",
    "run_due_automations",
    "save_automation_definition",
    "write_history_record",
]
