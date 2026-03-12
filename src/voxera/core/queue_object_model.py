"""Canonical lightweight contracts for queue job/object-model semantics.

These constants are intentionally additive and non-disruptive. They centralize
names that represent queue lifecycle and artifact/evidence concepts so docs,
prompts, and runtime helpers can share one vocabulary.
"""

from __future__ import annotations

from typing import Final, Literal

QueueLifecycleState = Literal[
    "queued",
    "planning",
    "running",
    "awaiting_approval",
    "resumed",
    "advisory_running",
    "done",
    "failed",
    "step_failed",
    "blocked",
    "canceled",
]

QUEUE_LIFECYCLE_STATES: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "planning",
        "running",
        "awaiting_approval",
        "resumed",
        "advisory_running",
        "done",
        "failed",
        "step_failed",
        "blocked",
        "canceled",
    }
)

COMPLETED_AT_LIFECYCLE_STATES: Final[frozenset[str]] = frozenset(
    {
        "done",
        "step_failed",
        "blocked",
        "canceled",
    }
)

TerminalOutcome = Literal["succeeded", "failed", "blocked", "denied", "canceled"]

TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"succeeded", "failed", "blocked", "denied", "canceled"}
)

ArtifactFamily = Literal[
    "plan",
    "actions",
    "stdout",
    "stderr",
    "review_summary",
    "approval",
    "evidence_bundle",
    "execution_envelope",
    "execution_result",
    "step_results",
    "assistant_advisory",
    "job_intent",
]

ARTIFACT_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "plan",
        "actions",
        "stdout",
        "stderr",
        "review_summary",
        "approval",
        "evidence_bundle",
        "execution_envelope",
        "execution_result",
        "step_results",
        "assistant_advisory",
        "job_intent",
    }
)

TRUTH_SURFACES: Final[dict[str, str]] = {
    "conversation": "interaction aid only; never authoritative for runtime outcomes",
    "preview": "authoritative draft state before submit",
    "queue": "authoritative submitted lifecycle/progression state",
    "artifact_evidence": "authoritative runtime-grounded post-execution outcome proof",
}
