"""Extraction-contract tests for the panel job-detail shaping cluster.

Scope: proves that moving the job-detail shaping cluster out of
``voxera.panel.app`` into ``voxera.panel.job_detail_sections`` /
``voxera.panel.job_presentation`` was done correctly and can't be
silently undone by a later panel-decomposition PR.

These tests are deliberately narrow. The HTTP-level behavior of
``GET /jobs/{job_id}``, ``GET /jobs/{job_id}/progress``, and the jobs
listing page is already covered by ``tests/test_panel.py``; this file
pins the *shape* of the extraction so that the existing HTTP coverage
keeps meaning what it used to mean:

1. ``job_detail_sections.py`` owns ``build_job_detail_payload``,
   ``build_job_progress_payload``, and ``build_job_detail_sections`` as
   the documented entry points.
2. ``job_presentation.py`` owns the tiny ``job_artifact_flags`` helper
   that powers the per-row artifact chips on ``GET /jobs``.
3. ``panel.app`` still visibly wires those entry points into its thin
   wrapper callbacks (``_job_detail_payload``, ``_job_progress_payload``,
   ``_job_artifact_flags``) and each wrapper's source visibly forwards
   to the extracted builder — no inline re-implementation.
4. ``panel.app`` no longer defines the private helper bodies inline
   (``_artifact_text``, ``_safe_json``, ``_load_actions``,
   ``_read_generated_files``, ``_payload_lineage``): those live behind
   ``job_detail_sections`` now.
5. ``panel.app`` no longer imports ``tail`` / ``lookup_job`` /
   ``queue_snapshot`` / ``resolve_structured_execution`` directly — the
   builder is the single panel-side caller of those primitives.
6. ``job_detail_sections.py`` does NOT reach back into ``panel.app``
   via any import (explicit-args architecture invariant, matching PR
   B's ``queue_mutation_bridge`` and PR C's
   ``security_health_helpers``).
7. Route contract is preserved: ``_job_detail_payload`` /
   ``_job_progress_payload`` / ``_job_artifact_flags`` still expose the
   same ``(queue_root, job_id) -> dict`` signature that
   ``register_job_routes`` expects.
8. Queue-truth precedence is preserved: ``execution.lifecycle_state``
   wins over ``state_sidecar.lifecycle_state`` wins over ``bucket`` in
   the progress payload.
9. 404 semantics preserved: ``build_job_detail_payload`` raises
   ``HTTPException(404, "job not found")`` when the job cannot be
   located and the artifacts directory does not exist.
10. Terminal-outcome filtering of the recent timeline is preserved:
    success-terminal jobs drop ``queue_job_failed`` /
    ``assistant_advisory_failed`` events; failed-terminal jobs drop
    ``queue_job_done`` / ``assistant_job_done`` events.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from voxera.panel import app as panel_module
from voxera.panel import job_detail_sections, job_presentation


def test_job_detail_sections_exposes_documented_entry_points() -> None:
    for name in (
        "build_job_detail_payload",
        "build_job_progress_payload",
        "build_job_detail_sections",
    ):
        assert hasattr(job_detail_sections, name), (
            f"job_detail_sections must expose {name!r} as a documented entry point."
        )
        assert callable(getattr(job_detail_sections, name))

    detail_sig = inspect.signature(job_detail_sections.build_job_detail_payload)
    assert list(detail_sig.parameters) == ["queue_root", "job_id"]

    progress_sig = inspect.signature(job_detail_sections.build_job_progress_payload)
    assert list(progress_sig.parameters) == ["queue_root", "job_id"]


def test_job_presentation_exposes_job_artifact_flags() -> None:
    assert hasattr(job_presentation, "job_artifact_flags")
    assert callable(job_presentation.job_artifact_flags)
    sig = inspect.signature(job_presentation.job_artifact_flags)
    assert list(sig.parameters) == ["queue_root", "job_id"]


def test_panel_app_wires_job_detail_shaping_entry_points() -> None:
    # panel.app must expose its thin wrapper callbacks and each wrapper
    # must forward to the extracted builder function — not re-implement
    # the logic locally.
    for name in (
        "_job_detail_payload",
        "_job_progress_payload",
        "_job_artifact_flags",
    ):
        assert callable(getattr(panel_module, name)), f"panel.app must still expose {name}"

    detail_wrapper = inspect.getsource(panel_module._job_detail_payload)
    assert "_build_job_detail_payload_impl(" in detail_wrapper

    progress_wrapper = inspect.getsource(panel_module._job_progress_payload)
    assert "_build_job_progress_payload_impl(" in progress_wrapper

    flags_wrapper = inspect.getsource(panel_module._job_artifact_flags)
    assert "_job_artifact_flags_impl(" in flags_wrapper


def test_panel_app_wrappers_preserve_route_callback_signatures() -> None:
    # register_job_routes expects (queue_root: Path, job_id: str) -> dict
    # for both the detail and progress payload callables and the artifact
    # flags callable. Pin the wrapper signatures so a later PR cannot
    # silently change the callback shape.
    detail_sig = inspect.signature(panel_module._job_detail_payload)
    assert list(detail_sig.parameters) == ["queue_root", "job_id"]

    progress_sig = inspect.signature(panel_module._job_progress_payload)
    assert list(progress_sig.parameters) == ["queue_root", "job_id"]

    flags_sig = inspect.signature(panel_module._job_artifact_flags)
    assert list(flags_sig.parameters) == ["queue_root", "job_id"]


def test_panel_app_no_longer_defines_extracted_private_helpers() -> None:
    # After extraction the small helpers coupled to the job-detail
    # builder must not be redefined in panel.app. Their homes are
    # private module-level functions inside job_detail_sections.py.
    for name in (
        "_artifact_text",
        "_safe_json",
        "_load_actions",
        "_read_generated_files",
        "_payload_lineage",
    ):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer expose {name} after the job-detail shaping extraction; "
            f"it now lives behind voxera.panel.job_detail_sections."
        )


def test_panel_app_no_longer_imports_job_detail_primitives_directly() -> None:
    # After extraction, job_detail_sections.py is the only panel-side
    # caller of ``tail`` / ``lookup_job`` / ``queue_snapshot`` /
    # ``resolve_structured_execution``. Pin that ``panel.app`` does not
    # re-import them so a later PR cannot silently reintroduce a local
    # inline implementation bypassing the extraction.
    for name in ("tail", "lookup_job", "queue_snapshot", "resolve_structured_execution"):
        assert not hasattr(panel_module, name), (
            f"panel.app should no longer import {name} directly; "
            f"it now lives behind voxera.panel.job_detail_sections."
        )


def test_job_detail_sections_does_not_reach_back_into_panel_app() -> None:
    # Architecture invariant: like PR B's queue_mutation_bridge and
    # PR C's security_health_helpers, this module is pure — every input
    # is explicit. A future PR that sneaks in a ``from . import app``
    # would quietly reintroduce a circular dependency and hide state.
    # Catch it here via AST so docstrings / comments that mention
    # "panel.app" don't false-positive.
    tree = ast.parse(inspect.getsource(job_detail_sections))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 1 and mod == "app":
                imported_modules.add(".app")
            if node.level >= 1 and mod.startswith("routes_"):
                imported_modules.add(f".{mod}")
            if mod == "voxera.panel.app":
                imported_modules.add("voxera.panel.app")
            if mod.startswith("voxera.panel.routes_"):
                imported_modules.add(mod)
    assert ".app" not in imported_modules
    assert "voxera.panel.app" not in imported_modules
    assert not any(m.startswith(".routes_") for m in imported_modules)
    assert not any(m.startswith("voxera.panel.routes_") for m in imported_modules)


def test_build_job_detail_payload_raises_404_when_job_and_artifacts_missing(
    tmp_path: Path,
) -> None:
    queue_root = tmp_path / "queue"
    queue_root.mkdir(parents=True)
    for bucket in ("inbox", "pending", "done", "failed", "canceled"):
        (queue_root / bucket).mkdir(parents=True, exist_ok=True)

    with pytest.raises(HTTPException) as exc_info:
        job_detail_sections.build_job_detail_payload(queue_root, "ghost.json")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "job not found"


def test_job_artifact_flags_reports_presence_flags(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    artifacts = queue_root / "artifacts" / "job-flags"
    artifacts.mkdir(parents=True)
    (artifacts / "plan.json").write_text("{}", encoding="utf-8")
    (artifacts / "stdout.txt").write_text("hi", encoding="utf-8")

    flags = job_presentation.job_artifact_flags(queue_root, "job-flags.json")
    assert flags == {
        "plan": True,
        "actions": False,
        "stdout": True,
        "stderr": False,
    }


def test_build_job_progress_payload_preserves_queue_truth_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Queue-truth precedence invariant: the structured execution
    # artifact (execution_result.json) wins over the state sidecar and
    # the raw bucket for lifecycle_state / terminal_outcome, and the
    # success-terminal recent-timeline filter still drops stale failure
    # events.
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True, exist_ok=True)
    (queue_root / "done" / "job-precedence.json").write_text('{"goal":"ok"}', encoding="utf-8")
    (queue_root / "done" / "job-precedence.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "failed",
                "terminal_outcome": "failed",
            }
        ),
        encoding="utf-8",
    )
    art = queue_root / "artifacts" / "job-precedence"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "execution_lane": "queue",
            }
        ),
        encoding="utf-8",
    )
    (art / "actions.jsonl").write_text(
        json.dumps({"event": "assistant_advisory_failed", "status": "failed"}) + "\n",
        encoding="utf-8",
    )

    payload = job_detail_sections.build_job_progress_payload(queue_root, "job-precedence.json")
    # Structured execution wins over state sidecar's stale failed state.
    assert payload["lifecycle_state"] == "done"
    assert payload["terminal_outcome"] == "succeeded"
    assert payload["bucket"] == "done"
    # Success-terminal filter removes the stale failed event from the timeline.
    events = {str(item.get("event") or "") for item in payload["recent_timeline"]}
    assert "assistant_advisory_failed" not in events
    # Success-terminal jobs never surface failure/stop-reason fields.
    assert payload["failure_summary"] is None
    assert payload["stop_reason"] is None


def test_build_job_progress_payload_matches_detail_payload_on_shared_keys(
    tmp_path: Path,
) -> None:
    # Sanity-check that the progress payload still derives from the
    # detail payload — they should agree on job_id / bucket / lineage
    # passthrough. This pins the build_job_progress_payload →
    # build_job_detail_payload composition shape.
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True, exist_ok=True)
    (queue_root / "done" / "job-sanity.json").write_text(
        json.dumps(
            {
                "goal": "ok",
                "parent_job_id": "root-1.json",
                "root_job_id": "root-1.json",
                "orchestration_depth": 1,
                "lineage_role": "child",
            }
        ),
        encoding="utf-8",
    )
    art = queue_root / "artifacts" / "job-sanity"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}),
        encoding="utf-8",
    )

    detail = job_detail_sections.build_job_detail_payload(queue_root, "job-sanity.json")
    progress = job_detail_sections.build_job_progress_payload(queue_root, "job-sanity.json")
    assert progress["job_id"] == detail["job_id"]
    assert progress["bucket"] == detail["bucket"]
    assert progress["lineage"] == detail["lineage"]
    assert progress["lineage"] is not None
    assert progress["lineage"]["parent_job_id"] == "root-1.json"


def test_panel_app_thin_wrappers_forward_to_extracted_builders(tmp_path: Path) -> None:
    # The thin wrappers in panel.app must forward the (queue_root, job_id)
    # arguments through to the extracted builders, preserving the
    # route-callback contract exactly.
    queue_root = tmp_path / "queue"
    art = queue_root / "artifacts" / "job-wrap"
    art.mkdir(parents=True)
    (art / "plan.json").write_text("{}", encoding="utf-8")

    # _job_artifact_flags wrapper forwards to job_presentation.job_artifact_flags.
    flags = panel_module._job_artifact_flags(queue_root, "job-wrap.json")
    assert flags == job_presentation.job_artifact_flags(queue_root, "job-wrap.json")

    # _job_detail_payload wrapper forwards to build_job_detail_payload
    # (demonstrated by matching 404 semantics on an unknown id).
    with pytest.raises(HTTPException) as exc_info:
        panel_module._job_detail_payload(queue_root, "ghost.json")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Payload key-set shape locks
#
# These two tests pin the *exact* top-level key sets of the job-detail and
# job-progress payloads. They are deliberately strong so that a later PR
# that silently adds, removes, or renames a payload key cannot land
# without also updating these pins. The rest of the extraction tests only
# pin structural invariants; these lock shape.
#
# If you are intentionally adding a new top-level key (for example the
# future shared session-context / ``vera_context`` block), update the
# corresponding frozenset below **and** the matching template consumers
# in the same PR.
# ---------------------------------------------------------------------------


_EXPECTED_JOB_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "job_id",
        "bucket",
        "job",
        "approval",
        "state",
        "failed_sidecar",
        "lock",
        "paused",
        "plan",
        "actions",
        "stdout",
        "stderr",
        "generated_files",
        "artifact_files",
        "artifact_inventory",
        "artifact_anomalies",
        "job_context",
        "lineage",
        "child_refs",
        "child_summary",
        "execution",
        "operator_summary",
        "policy_rationale",
        "evidence_summary",
        "why_stopped",
        "recent_timeline",
        "artifacts_dir",
        "audit_timeline",
        "has_approval",
        "can_cancel",
        "can_retry",
        "can_delete",
    }
)


_EXPECTED_JOB_PROGRESS_KEYS: frozenset[str] = frozenset(
    {
        "ok",
        "job_id",
        "bucket",
        "lifecycle_state",
        "terminal_outcome",
        "current_step_index",
        "total_steps",
        "last_attempted_step",
        "last_completed_step",
        "approval_status",
        "execution_lane",
        "fast_lane",
        "intent_route",
        "lineage",
        "child_refs",
        "child_summary",
        "parent_job_id",
        "root_job_id",
        "orchestration_depth",
        "sequence_index",
        "latest_summary",
        "operator_note",
        "operator_summary",
        "failure_summary",
        "stop_reason",
        "artifacts",
        "step_summaries",
        "recent_timeline",
    }
)


def test_job_detail_payload_key_set_shape_lock(tmp_path: Path) -> None:
    # Shape lock: the full set of top-level keys returned by
    # build_job_detail_payload must exactly match the documented set.
    # A later PR that silently adds, renames, or removes a key must
    # update this pin in the same commit.
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True)
    (queue_root / "done" / "job-shape.json").write_text('{"goal":"ok"}', encoding="utf-8")
    art = queue_root / "artifacts" / "job-shape"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}),
        encoding="utf-8",
    )

    payload = job_detail_sections.build_job_detail_payload(queue_root, "job-shape.json")
    assert set(payload.keys()) == set(_EXPECTED_JOB_DETAIL_KEYS)


def test_job_progress_payload_key_set_shape_lock(tmp_path: Path) -> None:
    # Shape lock: the full set of top-level keys returned by
    # build_job_progress_payload must exactly match the documented set.
    # A later PR that silently adds, renames, or removes a key must
    # update this pin in the same commit.
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True)
    (queue_root / "done" / "job-shape.json").write_text('{"goal":"ok"}', encoding="utf-8")
    art = queue_root / "artifacts" / "job-shape"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}),
        encoding="utf-8",
    )

    payload = job_detail_sections.build_job_progress_payload(queue_root, "job-shape.json")
    assert set(payload.keys()) == set(_EXPECTED_JOB_PROGRESS_KEYS)
