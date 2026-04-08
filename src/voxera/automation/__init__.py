"""Automation object model and storage foundation.

This package hosts the durable automation definition model and its
file-backed storage layer. It is intentionally a *definition* layer only:
it does not execute skills, does not submit queue jobs, and does not run
any scheduler. A future PR may add a runner that reads these definitions
and emits normal queue jobs from them. Until then, automation definitions
are inert, governed records.

See ``docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`` for the queue payload
shape that ``AutomationDefinition.payload_template`` must conform to.
"""

from __future__ import annotations

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
    "AUTOMATION_ID_PATTERN",
    "AUTOMATION_POLICY_POSTURES",
    "AUTOMATION_TRIGGER_KINDS",
    "DEFINITIONS_DIRNAME",
    "HISTORY_DIRNAME",
    "WATCH_PATH_ALLOWED_EVENTS",
    "AutomationCreatedFrom",
    "AutomationDefinition",
    "AutomationNotFoundError",
    "AutomationPolicyPosture",
    "AutomationStoreError",
    "AutomationTriggerKind",
    "automations_root",
    "definition_path",
    "definitions_dir",
    "delete_automation_definition",
    "ensure_automation_dirs",
    "history_dir",
    "list_automation_definitions",
    "load_automation_definition",
    "save_automation_definition",
]
