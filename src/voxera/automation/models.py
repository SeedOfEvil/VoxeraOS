"""Canonical automation definition data model.

Automation in VoxeraOS means *governed deferred queue submission*, nothing else.
An automation definition is a durable declaration that, at some future time or
in response to some future trigger, a normal canonical queue payload should be
submitted to the queue. The queue remains the execution boundary.

This module deliberately only describes the durable object and validates it.
It does not run anything, does not submit anything, and does not interact with
the queue daemon. A future PR may add a runner that emits normal queue jobs
from saved definitions; this PR is the object model foundation only.

Validation rules (fail-closed by design):

- ``trigger_kind`` must be one of the supported kinds.
- ``trigger_config`` must be a dict whose shape matches ``trigger_kind``.
- ``payload_template`` must be a non-empty dict that carries at least one
  canonical top-level queue request family field (``mission_id``, ``goal``,
  ``steps``, ``file_organize``, ``write_file``) and must validate against the
  existing queue contract helpers for any of those fields that are present.
- Unknown trigger kinds, malformed trigger config, or payload templates that
  do not look like canonical queue requests are rejected.
"""

from __future__ import annotations

import time
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.queue_contracts import (
    extract_file_organize_request,
    extract_write_file_request,
)

AutomationTriggerKind = Literal[
    "once_at",
    "delay",
    "recurring_interval",
    "recurring_cron",
    "watch_path",
]

AUTOMATION_TRIGGER_KINDS: Final[frozenset[str]] = frozenset(
    {
        "once_at",
        "delay",
        "recurring_interval",
        "recurring_cron",
        "watch_path",
    }
)

AutomationCreatedFrom = Literal["vera", "panel", "cli"]

AUTOMATION_CREATED_FROM_VALUES: Final[frozenset[str]] = frozenset({"vera", "panel", "cli"})

AutomationPolicyPosture = Literal["standard", "strict_review"]

AUTOMATION_POLICY_POSTURES: Final[frozenset[str]] = frozenset({"standard", "strict_review"})

# Canonical top-level queue request family fields that a payload_template is
# allowed to carry as its request-kind anchor. At least one must be present.
# This matches the canonical queue payload surface documented in
# ``docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`` and implemented in
# ``src/voxera/core/queue_contracts.py``.
AUTOMATION_CANONICAL_REQUEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "mission_id",
        "goal",
        "steps",
        "file_organize",
        "write_file",
    }
)

WATCH_PATH_ALLOWED_EVENTS: Final[frozenset[str]] = frozenset({"created", "modified", "deleted"})


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _validate_trigger_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Per-trigger-kind config validation. Fails closed on anything unexpected."""
    if kind == "once_at":
        allowed = {"run_at_ms"}
        unknown = sorted(set(config.keys()) - allowed)
        if unknown:
            raise ValueError(
                f"trigger_config for once_at contains unsupported keys: {', '.join(unknown)}"
            )
        raw = config.get("run_at_ms")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ValueError("trigger_config.run_at_ms must be a positive int epoch-ms")
        return {"run_at_ms": raw}

    if kind == "delay":
        allowed = {"delay_ms"}
        unknown = sorted(set(config.keys()) - allowed)
        if unknown:
            raise ValueError(
                f"trigger_config for delay contains unsupported keys: {', '.join(unknown)}"
            )
        raw = config.get("delay_ms")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ValueError("trigger_config.delay_ms must be a positive int millisecond value")
        return {"delay_ms": raw}

    if kind == "recurring_interval":
        allowed = {"interval_ms"}
        unknown = sorted(set(config.keys()) - allowed)
        if unknown:
            raise ValueError(
                "trigger_config for recurring_interval contains unsupported keys: "
                f"{', '.join(unknown)}"
            )
        raw = config.get("interval_ms")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ValueError("trigger_config.interval_ms must be a positive int millisecond value")
        return {"interval_ms": raw}

    if kind == "recurring_cron":
        allowed = {"cron"}
        unknown = sorted(set(config.keys()) - allowed)
        if unknown:
            raise ValueError(
                f"trigger_config for recurring_cron contains unsupported keys: {', '.join(unknown)}"
            )
        cron = _clean_str(config.get("cron"))
        if cron is None:
            raise ValueError("trigger_config.cron must be a non-empty string")
        # Intentionally conservative: we accept the string at definition time
        # and defer actual cron parsing to the future runner. We still reject
        # obviously empty or non-string values above.
        return {"cron": cron}

    if kind == "watch_path":
        allowed = {"path", "event"}
        unknown = sorted(set(config.keys()) - allowed)
        if unknown:
            raise ValueError(
                f"trigger_config for watch_path contains unsupported keys: {', '.join(unknown)}"
            )
        path = _clean_str(config.get("path"))
        if path is None:
            raise ValueError("trigger_config.path must be a non-empty string")
        event = _clean_str(config.get("event")) or "created"
        if event not in WATCH_PATH_ALLOWED_EVENTS:
            allowed_events = ", ".join(sorted(WATCH_PATH_ALLOWED_EVENTS))
            raise ValueError(f"trigger_config.event must be one of: {allowed_events}")
        return {"path": path, "event": event}

    # Unknown trigger kinds are already rejected earlier in the model; this
    # branch exists so ``_validate_trigger_config`` itself is fail-closed.
    raise ValueError(f"unknown trigger_kind: {kind}")


def _validate_payload_template(payload: dict[str, Any]) -> None:
    """Require payload_template to look like a canonical queue request.

    We intentionally reuse the existing queue contract extractors where
    possible so the automation layer never drifts from the queue's shape.
    """
    if not isinstance(payload, dict):  # pragma: no cover - pydantic gates this
        raise ValueError("payload_template must be an object/dict")
    if not payload:
        raise ValueError("payload_template must not be empty")

    present_request_fields = {
        field for field in AUTOMATION_CANONICAL_REQUEST_FIELDS if field in payload
    }
    if not present_request_fields:
        allowed = ", ".join(sorted(AUTOMATION_CANONICAL_REQUEST_FIELDS))
        raise ValueError(
            "payload_template must carry at least one canonical queue request field "
            f"(one of: {allowed})"
        )

    if "mission_id" in payload:
        mission_id = _clean_str(payload.get("mission_id"))
        if mission_id is None:
            raise ValueError("payload_template.mission_id must be a non-empty string")

    if "goal" in payload:
        goal = _clean_str(payload.get("goal"))
        if goal is None:
            raise ValueError("payload_template.goal must be a non-empty string")

    if "steps" in payload:
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("payload_template.steps must be a non-empty list when provided")
        for idx, item in enumerate(steps):
            if not isinstance(item, dict):
                raise ValueError(f"payload_template.steps[{idx}] must be an object")

    if "write_file" in payload:
        # Delegate to the canonical queue contract extractor; it raises
        # ValueError on malformed shapes, which we let propagate.
        extract_write_file_request(payload)

    if "file_organize" in payload:
        extract_file_organize_request(payload)


class AutomationDefinition(BaseModel):
    """Durable automation definition.

    An automation definition describes a *future* queue submission. It does
    not execute anything by itself. A runnable trigger still needs a runner
    (not included in this PR) to actually emit a queue job.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    enabled: bool = True
    trigger_kind: AutomationTriggerKind
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    payload_template: dict[str, Any] = Field(default_factory=dict)
    created_at_ms: int = Field(default_factory=_now_ms)
    updated_at_ms: int = Field(default_factory=_now_ms)
    last_run_at_ms: int | None = None
    next_run_at_ms: int | None = None
    last_job_ref: str | None = None
    run_history_refs: list[str] = Field(default_factory=list)
    policy_posture: AutomationPolicyPosture = "standard"
    created_from: AutomationCreatedFrom = "cli"

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("id must be a non-empty string")
        # Deterministic, filesystem-safe id. We keep this narrow on purpose so
        # the on-disk filename is simply ``<id>.json``.
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if any(ch not in allowed_chars for ch in stripped):
            raise ValueError("id may only contain ASCII letters, digits, underscores, and hyphens")
        if len(stripped) > 128:
            raise ValueError("id must be at most 128 characters")
        return stripped

    @field_validator("title")
    @classmethod
    def _title_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must be a non-empty string")
        return stripped

    @field_validator("description")
    @classmethod
    def _description_is_string(cls, value: str) -> str:
        return value if isinstance(value, str) else ""

    @field_validator("created_at_ms", "updated_at_ms")
    @classmethod
    def _timestamp_positive(cls, value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("timestamp must be a positive int epoch-ms")
        return value

    @field_validator("last_run_at_ms", "next_run_at_ms")
    @classmethod
    def _optional_timestamp(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("timestamp must be a positive int epoch-ms when provided")
        return value

    @field_validator("run_history_refs")
    @classmethod
    def _run_history_refs_shape(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("run_history_refs items must be strings")
            stripped = item.strip()
            if not stripped:
                raise ValueError("run_history_refs items must be non-empty strings")
            cleaned.append(stripped)
        return cleaned

    @model_validator(mode="after")
    def _cross_field_validation(self) -> AutomationDefinition:
        if self.trigger_kind not in AUTOMATION_TRIGGER_KINDS:
            raise ValueError(f"unknown trigger_kind: {self.trigger_kind!r}")

        if not isinstance(self.trigger_config, dict):
            raise ValueError("trigger_config must be an object/dict")

        # Normalize / validate per kind. We replace the stored dict with the
        # validated (narrowed) shape so downstream consumers do not re-parse.
        normalized_trigger_config = _validate_trigger_config(
            self.trigger_kind, dict(self.trigger_config)
        )
        object.__setattr__(self, "trigger_config", normalized_trigger_config)

        _validate_payload_template(self.payload_template)

        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must be >= created_at_ms")

        return self

    def touch_updated(self, *, now_ms: int | None = None) -> AutomationDefinition:
        """Return a copy with ``updated_at_ms`` refreshed.

        We return a copy rather than mutating in place so callers that hold a
        prior reference still see the original snapshot — this matches the
        immutable-feeling style used elsewhere in the repo for queue records.
        """
        stamp = int(now_ms) if now_ms is not None else _now_ms()
        if stamp < self.created_at_ms:
            stamp = self.created_at_ms
        return self.model_copy(update={"updated_at_ms": stamp})


__all__ = [
    "AUTOMATION_CANONICAL_REQUEST_FIELDS",
    "AUTOMATION_CREATED_FROM_VALUES",
    "AUTOMATION_POLICY_POSTURES",
    "AUTOMATION_TRIGGER_KINDS",
    "AutomationCreatedFrom",
    "AutomationDefinition",
    "AutomationPolicyPosture",
    "AutomationTriggerKind",
    "WATCH_PATH_ALLOWED_EVENTS",
]
