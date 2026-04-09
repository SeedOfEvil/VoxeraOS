"""File-backed storage for automation definitions.

This is deliberately boring: one JSON file per automation definition under
``<queue_root>/automations/definitions/<id>.json``. A sibling ``history/``
directory is created for future runner use; PR1 does not write anything into
it. No caching, no indexing, no locking beyond the filesystem itself.

Fail-closed semantics:

- ``load_automation_definition`` raises on missing files, unreadable files,
  or payloads that fail ``AutomationDefinition`` validation.
- ``list_automation_definitions`` defaults to best-effort: a malformed file
  on disk is skipped (not raised) so one bad file cannot hide the rest of
  the automation inventory from operators. Strict mode is available for
  tests and tooling that want to surface every problem.
- ``definition_path`` enforces ``AUTOMATION_ID_PATTERN`` so caller input
  (CLI / HTTP path param) cannot escape the definitions directory, even
  before an ``AutomationDefinition`` has been constructed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import AUTOMATION_ID_PATTERN, AutomationDefinition

AUTOMATIONS_DIRNAME = "automations"
DEFINITIONS_DIRNAME = "definitions"
HISTORY_DIRNAME = "history"

_TMP_SUFFIX = ".tmp"


class AutomationStoreError(RuntimeError):
    """Raised when an automation cannot be loaded, saved, or located."""


class AutomationNotFoundError(AutomationStoreError):
    """Raised when a requested automation id is not present on disk."""


def automations_root(queue_root: Path) -> Path:
    """Return the automations root directory under a given queue root."""
    return Path(queue_root).expanduser() / AUTOMATIONS_DIRNAME


def definitions_dir(queue_root: Path) -> Path:
    return automations_root(queue_root) / DEFINITIONS_DIRNAME


def history_dir(queue_root: Path) -> Path:
    return automations_root(queue_root) / HISTORY_DIRNAME


def ensure_automation_dirs(queue_root: Path) -> Path:
    """Create the automation storage directories if missing.

    Returns the automations root directory.
    """
    root = automations_root(queue_root)
    root.mkdir(parents=True, exist_ok=True)
    definitions_dir(queue_root).mkdir(parents=True, exist_ok=True)
    history_dir(queue_root).mkdir(parents=True, exist_ok=True)
    return root


def _validated_id(automation_id: str) -> str:
    """Return ``automation_id`` if it matches ``AUTOMATION_ID_PATTERN``.

    Raises ``AutomationStoreError`` otherwise. This is intentionally stricter
    than a simple path-traversal check so the set of legal on-disk filenames
    is identical to the set of legal ``AutomationDefinition.id`` values.
    """
    if not isinstance(automation_id, str):
        raise AutomationStoreError("automation id must be a string")
    stripped = automation_id.strip()
    if not stripped:
        raise AutomationStoreError("automation id must be a non-empty string")
    if not AUTOMATION_ID_PATTERN.match(stripped):
        raise AutomationStoreError(f"invalid automation id: {automation_id!r}")
    return stripped


def definition_path(queue_root: Path, automation_id: str) -> Path:
    """Return the on-disk path for a given automation id.

    The id is validated against ``AUTOMATION_ID_PATTERN`` before it is joined
    to the definitions directory, so traversal segments, path separators,
    null bytes, and leading dots are all rejected fail-closed.
    """
    return definitions_dir(queue_root) / f"{_validated_id(automation_id)}.json"


def save_automation_definition(
    definition: AutomationDefinition,
    queue_root: Path,
    *,
    touch_updated: bool = True,
    now_ms: int | None = None,
) -> Path:
    """Persist an automation definition to disk.

    When ``touch_updated`` is True (the default), ``updated_at_ms`` is
    refreshed to the current time before writing so repeated saves reflect
    the latest edit. Set it to False when callers want to preserve an
    existing ``updated_at_ms`` (e.g. an import/migration path).

    The write is atomic: we serialize to a ``.tmp`` sibling and then
    ``Path.replace`` onto the final target, so a crashed writer can never
    leave a half-written JSON file where a reader would see it. The
    ``.tmp`` sidecar lives in the definitions directory but is excluded
    from ``list_automation_definitions`` because the glob matches
    ``*.json``, not ``*.json.tmp``.
    """
    ensure_automation_dirs(queue_root)
    target = definition_path(queue_root, definition.id)

    to_write = definition.touch_updated(now_ms=now_ms) if touch_updated else definition
    payload = to_write.model_dump(mode="json")
    tmp = target.with_suffix(target.suffix + _TMP_SUFFIX)
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def load_automation_definition(
    automation_id: str,
    queue_root: Path,
) -> AutomationDefinition:
    """Load and validate an automation definition by id.

    Raises:
        AutomationStoreError: if the id is not legal or the file is
            present but unreadable or fails validation.
        AutomationNotFoundError: if the id is legal but the file is absent.
    """
    path = definition_path(queue_root, automation_id)
    if not path.exists():
        raise AutomationNotFoundError(f"automation not found: {automation_id}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AutomationStoreError(f"failed to read automation file {path}: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AutomationStoreError(f"automation file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AutomationStoreError(f"automation file {path} must contain a JSON object")
    try:
        return AutomationDefinition.model_validate(data)
    except ValidationError as exc:
        raise AutomationStoreError(f"automation file {path} failed validation: {exc}") from exc


def list_automation_definitions(
    queue_root: Path,
    *,
    strict: bool = False,
) -> list[AutomationDefinition]:
    """Return every valid automation definition under the queue root.

    In the default best-effort mode, malformed files on disk are silently
    skipped so one bad file cannot hide the rest of the inventory. In
    ``strict`` mode, the first malformed file raises ``AutomationStoreError``.

    Results are sorted by ``id`` for deterministic output. Temporary
    ``*.json.tmp`` files left behind by an interrupted save are excluded.
    """
    directory = definitions_dir(queue_root)
    if not directory.exists():
        return []

    definitions: list[AutomationDefinition] = []
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data: Any = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("automation file must contain a JSON object")
            definition = AutomationDefinition.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError, ValidationError) as exc:
            if strict:
                raise AutomationStoreError(f"automation file {path} failed to load: {exc}") from exc
            continue
        definitions.append(definition)

    definitions.sort(key=lambda item: item.id)
    return definitions


def delete_automation_definition(
    automation_id: str,
    queue_root: Path,
    *,
    missing_ok: bool = False,
) -> bool:
    """Delete an automation definition file.

    Returns True if a file was actually removed. When ``missing_ok`` is
    False (the default) and the file is absent, ``AutomationNotFoundError``
    is raised so callers can tell the difference between a no-op and a
    real delete.
    """
    path = definition_path(queue_root, automation_id)
    if not path.exists():
        if missing_ok:
            return False
        raise AutomationNotFoundError(f"automation not found: {automation_id}")
    try:
        path.unlink()
    except OSError as exc:
        raise AutomationStoreError(f"failed to delete automation file {path}: {exc}") from exc
    return True


__all__ = [
    "AUTOMATIONS_DIRNAME",
    "DEFINITIONS_DIRNAME",
    "HISTORY_DIRNAME",
    "AutomationNotFoundError",
    "AutomationStoreError",
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
